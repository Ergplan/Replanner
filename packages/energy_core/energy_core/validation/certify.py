"""Independent full-resolution certification.

This module deliberately does not ask the solver whether it was right. It takes the
immutable spec and the exported decisions and recomputes the physics and the money from
scratch, block by block, across every interval of the operating year. If the two
disagree, the solver's answer is what is wrong.

That independence is the entire point. A constraint residual read back out of the model
only proves the solver satisfied the constraints it was given; it cannot catch a
constraint that was written incorrectly, a unit that was converted twice, or a cost that
was never added to the objective at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..schemas.common import Mode, SolveStatus, ValidationStatus
from ..schemas.results import CapacityResult, CostLedger, ValidationIssue, ValidationReport
from ..ingestion import expected_blocks
from ..optimization.run import SIMUL_TOL_MW
from ..optimization.spec import RunSpec


@dataclass
class Tolerances:
    """Declared per unit and justified by plant scale.

    A site of this size dispatches tens of MW and spends hundreds of millions of rupees a
    year. A micro-watt of imbalance is floating-point noise; a kilowatt is a modelling
    error. Money is checked relatively, because an absolute rupee tolerance is meaningless
    against a nine-figure total.
    """

    power_mw: float = 1e-6
    energy_mwh: float = 1e-6
    money_rel: float = 1e-8
    money_abs_inr: float = 1.0

    def as_dict(self) -> dict[str, float]:
        return {"power_mw": self.power_mw, "energy_mwh": self.energy_mwh,
                "money_rel": self.money_rel, "money_abs_inr": self.money_abs_inr}


def _issue(check: str, residual: float, tol: float, units: str, detail: str,
           idx: int | None = None, ts=None) -> ValidationIssue:
    return ValidationIssue(
        check=check, severity="material" if residual > tol else "roundoff",
        timestamp_utc=ts, block_index=idx, residual=float(residual), tolerance=float(tol),
        units=units, detail=detail)


def _worst(name: str, resid: np.ndarray, tol: float, units: str, spec: RunSpec,
           issues: list[ValidationIssue], maxres: dict[str, float], detail: str,
           *, two_sided: bool = True) -> None:
    """Record the largest violation of one check, and count how many there were.

    Equalities are violated in either direction, so their residual is taken absolutely.
    Inequalities are not: sitting comfortably below a ceiling is the normal case, and
    treating that slack as a violation would fail every correct run.
    """
    a = np.abs(resid) if two_sided else np.maximum(resid, 0.0)
    maxres[name] = float(a.max()) if a.size else 0.0
    bad = np.flatnonzero(a > tol)
    if bad.size:
        k = int(bad[np.argmax(a[bad])])
        issues.append(_issue(name, float(a[k]), tol, units,
                             f"{detail}; {bad.size:,} block(s) exceed tolerance",
                             idx=k, ts=spec.timestamps_utc[k]))


def certify(spec: RunSpec, caps: CapacityResult, dispatch: pd.DataFrame, ledger: CostLedger,
            *, solver_objective: float | None, status: SolveStatus, gap: float | None,
            run_id: str = "", tol: Tolerances | None = None,
            solver_version: str = "", horizon_blocks: int | None = None) -> ValidationReport:
    """`horizon_blocks` overrides the expected block count, and exists only for the
    analytical fixtures, which are a few hours long by design. A real run leaves it None
    so the expected count comes from the operating year and cannot be talked down."""
    tol = tol or Tolerances()
    issues: list[ValidationIssue] = []
    maxres: dict[str, float] = {}
    checks: list[str] = []
    dt = spec.dt
    n = spec.n

    rep = ValidationReport(
        run_id=run_id,
        blocks_expected=(horizon_blocks if horizon_blocks is not None
                         else expected_blocks(spec.operating_year, spec.timezone)),
        input_hash=spec.fingerprint(), model_version=spec.model_version,
        solver_version=solver_version, tolerances=tol.as_dict(),
        generated_at=datetime.now(timezone.utc))

    # ---- 1. coverage and boundaries ------------------------------------------------
    checks.append("interval_coverage")
    ts = pd.DatetimeIndex(dispatch["timestamp_utc"])
    rep.blocks_checked = len(dispatch)
    if len(dispatch) != rep.blocks_expected or len(dispatch) != n:
        # Return here rather than press on: every check below is elementwise against the
        # spec's arrays, and a short frame would raise a shape error instead of the
        # verdict the caller asked for.
        issues.append(_issue("interval_coverage", abs(len(dispatch) - rep.blocks_expected), 0,
                             "blocks",
                             f"{len(dispatch):,} blocks exported, {rep.blocks_expected:,} expected"))
        rep.issues = issues
        rep.checks_run = checks
        rep.max_residual = maxres
        rep.status = ValidationStatus.FAILED
        return rep
    if not ts.equals(pd.DatetimeIndex(spec.timestamps_utc)):
        issues.append(_issue("interval_coverage", 1, 0, "blocks",
                             "exported timestamps do not match the canonical index"))
    if not np.allclose(dispatch["duration_hours"].to_numpy(), dt):
        issues.append(_issue("interval_coverage", 1, 0, "hours", "duration_hours is not 0.25"))

    col = lambda c: dispatch[c].to_numpy(dtype=float)
    imp_u, imp_x = col("import_utility_mw"), col("import_market_mw")
    exp_u, exp_x = col("export_utility_mw"), col("export_market_mw")
    ch, dis = col("battery_charge_mw"), col("battery_discharge_mw")
    soc_end = col("soc_end_mwh")
    soc_start = np.roll(soc_end, 1)
    aux = col("aux_mw")

    # ---- 2. renewable accounting, recomputed from capacity and profile ----------------
    checks.append("renewable_accounting")
    total_use = np.zeros(n)
    for g in spec.generation:
        cap_total = getattr(caps, g.cap_key) + g.existing_mw
        available = cap_total * g.profile * g.delivery_factor
        use, curt = col(f"use_{g.key}_mw"), col(f"curtail_{g.key}_mw")
        _worst(f"available_{g.key}", use + curt - available, tol.power_mw, "MW", spec, issues,
               maxres, f"used + curtailed must equal capacity x profile x delivery for {g.key}")
        _worst(f"nonneg_{g.key}", -np.minimum(use, 0.0), tol.power_mw, "MW", spec, issues,
               maxres, f"{g.key} used energy went negative", two_sided=False)
        total_use += use

    # ---- 3. energy balance ------------------------------------------------------------
    checks.append("energy_balance")
    supply = total_use + imp_u + imp_x + dis
    drain = spec.load_mw + ch + aux + exp_u + exp_x
    _worst("energy_balance", supply - drain, tol.power_mw, "MW", spec, issues, maxres,
           "supply + discharge must equal load + charge + auxiliaries + export")

    # ---- 4. storage --------------------------------------------------------------------
    checks.append("soc_transitions")
    b = spec.battery
    e_tot = caps.bess_energy_mwh + caps.existing_bess_energy_mwh
    p_tot = caps.bess_power_mw + caps.existing_bess_power_mw
    predicted = (b.retention_per_block * soc_start + b.eta_charge * ch * dt
                 - dis * dt / b.eta_discharge)
    _worst("soc_recursion", soc_end - predicted, tol.energy_mwh, "MWh", spec, issues, maxres,
           "every SOC transition, including the wrap from the last block to the first")
    _worst("soc_upper", soc_end - b.soc_max_frac * e_tot, tol.energy_mwh, "MWh", spec, issues,
           maxres, "SOC above the usable ceiling", two_sided=False)
    _worst("soc_lower", b.soc_min_frac * e_tot - soc_end, tol.energy_mwh, "MWh", spec, issues,
           maxres, "SOC below the usable floor", two_sided=False)
    _worst("charge_power", ch - p_tot, tol.power_mw, "MW", spec, issues, maxres,
           "charge above the PCS rating", two_sided=False)
    _worst("discharge_power", dis - p_tot, tol.power_mw, "MW", spec, issues, maxres,
           "discharge above the PCS rating", two_sided=False)
    checks.append("c_rate")
    if p_tot > b.max_c_rate * e_tot + tol.power_mw:
        issues.append(_issue("c_rate", p_tot - b.max_c_rate * e_tot, tol.power_mw, "MW",
                             f"power {p_tot:.4f} MW exceeds {b.max_c_rate} C on {e_tot:.4f} MWh"))
    checks.append("terminal_soc")
    # A cyclic year must not begin with energy it never stored. The recursion above already
    # wraps, so this restates it as an explicit, separately reported condition.
    wrap = soc_end[-1] - (soc_start[0])
    if abs(wrap) > tol.energy_mwh:
        issues.append(_issue("terminal_soc", abs(wrap), tol.energy_mwh, "MWh",
                             "terminal SOC does not feed the opening block"))

    # ---- 5. connection limits and prohibited simultaneity --------------------------------
    checks.append("interconnection")
    wheeled_use = sum(col(f"use_{g.key}_mw") for g in spec.generation if g.wheeled)
    if isinstance(wheeled_use, int):
        wheeled_use = np.zeros(n)
    limit = spec.import_limit_mw * spec.grid_available
    _worst("import_limit", imp_u + imp_x - limit, tol.power_mw, "MW", spec, issues, maxres,
           "utility, open access and market imports share one connection", two_sided=False)
    _worst("wheeled_limit", wheeled_use + imp_u + imp_x - limit, tol.power_mw, "MW", spec,
           issues, maxres, "wheeled generation also crosses the connection", two_sided=False)
    _worst("export_limit", exp_u + exp_x - spec.export_limit_mw * spec.grid_available,
           tol.power_mw, "MW", spec, issues, maxres, "export above the sanctioned limit",
           two_sided=False)

    checks.append("simultaneity")
    # Checked on every block, not only the ones the model was given binaries for.
    both_bat = np.flatnonzero((ch > SIMUL_TOL_MW) & (dis > SIMUL_TOL_MW))
    both_grid = np.flatnonzero(((imp_u + imp_x) > SIMUL_TOL_MW) & ((exp_u + exp_x) > SIMUL_TOL_MW))
    for name, idxs, detail in (("simultaneous_charge_discharge", both_bat,
                                "battery charging and discharging in the same block"),
                               ("simultaneous_import_export", both_grid,
                                "importing and exporting in the same block")):
        maxres[name] = float(len(idxs))
        if len(idxs):
            k = int(idxs[0])
            issues.append(_issue(name, float(len(idxs)), 0.0, "blocks",
                                 f"{detail}; {len(idxs):,} block(s)", idx=k,
                                 ts=spec.timestamps_utc[k]))

    # ---- 6. demand served in every block --------------------------------------------------
    checks.append("demand_served")
    unserved = col("unserved_load_mw")
    _worst("unserved_load", unserved, tol.power_mw, "MW", spec, issues, maxres,
           "all demand must be served unless a diagnostic relaxation run was requested",
           two_sided=False)
    _worst("served_load", col("served_load_mw") - spec.load_mw, tol.power_mw, "MW", spec,
           issues, maxres, "served load must equal the load profile")

    # ---- 7. billing and the cost ledger ----------------------------------------------------
    checks.append("monthly_billing")
    dem = np.array([imp_u[spec.month_index == m].max() if (spec.month_index == m).any() else 0.0
                    for m in range(spec.n_months)])
    dem = np.maximum(dem, spec.min_billable_demand_mw)

    checks.append("cost_ledger")
    re_ledger = CostLedger()
    for g in spec.generation:
        cap_new = getattr(caps, g.cap_key)
        re_ledger.annualised_capex_new += cap_new * g.annuity_inr_per_mw_year
        re_ledger.fixed_om += cap_new * g.fixed_om_inr_per_mw_year
        use = col(f"use_{g.key}_mw")
        re_ledger.variable_om += float(use.sum() * dt * g.variable_om_inr_per_mwh)
        if g.wheeled:
            re_ledger.open_access_charges += float(use.sum() * dt * spec.oa_charge_inr_per_mwh)
    re_ledger.annualised_capex_new += (caps.bess_power_mw * b.annuity_inr_per_mw_year
                                       + caps.bess_energy_mwh * b.annuity_inr_per_mwh_year)
    re_ledger.fixed_om += (caps.bess_power_mw * b.fixed_om_inr_per_mw_year
                           + spec.existing_fixed_om_inr_year)
    e_ex_duty = float((imp_u * dt * spec.energy_inr_per_mwh).sum())
    d_ex_duty = float(dem.sum() * spec.demand_rate_inr_per_mw_month)
    re_ledger.utility_energy = e_ex_duty
    re_ledger.utility_demand = d_ex_duty
    re_ledger.duty = (e_ex_duty + d_ex_duty) * spec.duty_frac
    re_ledger.market_purchase = float((imp_x * dt * spec.iex_buy_inr_per_mwh).sum())
    re_ledger.market_transaction = float(imp_x.sum() * dt * spec.market_txn_inr_per_mwh)
    if spec.market_oa_applies:
        re_ledger.open_access_charges += float(imp_x.sum() * dt * spec.oa_charge_inr_per_mwh)
    re_ledger.battery_wear = float(dis.sum() * dt * b.wear_inr_per_mwh_discharged)
    re_ledger.export_revenue = -(float((exp_u * dt * spec.export_inr_per_mwh).sum())
                                 + float((exp_x * dt * spec.iex_sell_inr_per_mwh).sum()))
    re_ledger.total_annual_cost = re_ledger.recompute_total()

    scale = max(abs(re_ledger.total_annual_cost), 1.0)
    money_tol = max(tol.money_abs_inr, tol.money_rel * scale)
    for k, v in re_ledger.components().items():
        d = abs(v - getattr(ledger, k))
        maxres[f"ledger_{k}"] = float(d)
        if d > money_tol:
            issues.append(_issue(f"ledger_{k}", d, money_tol, "INR",
                                 f"recomputed {v:,.2f} against reported {getattr(ledger, k):,.2f}"))

    checks.append("objective_reconciliation")
    if solver_objective is not None:
        d = abs(re_ledger.total_annual_cost - solver_objective)
        maxres["objective_reconciliation"] = float(d)
        if d > money_tol:
            issues.append(_issue("objective_reconciliation", d, money_tol, "INR",
                                 f"recomputed total {re_ledger.total_annual_cost:,.2f} against "
                                 f"solver objective {solver_objective:,.2f}"))

    # ---- 8. Mode B capacity identity ---------------------------------------------------------
    if spec.mode is Mode.MANUAL and spec.fixed_capacities:
        checks.append("fixed_capacity_identity")
        for key, want in spec.fixed_capacities.items():
            got = getattr(caps, key, None)
            if got is None:
                continue
            d = abs(float(got) - float(want))
            maxres[f"fixed_{key}"] = float(d)
            if d > 1e-9:
                issues.append(_issue(f"fixed_{key}", d, 1e-9, "MW/MWh",
                                     f"{key} solved to {got} but was fixed at {want}"))

    # ---- verdict --------------------------------------------------------------------------
    rep.issues = issues
    rep.checks_run = checks
    rep.max_residual = maxres
    material = [i for i in issues if i.severity == "material"]
    if material or rep.blocks_checked != rep.blocks_expected:
        rep.status = ValidationStatus.FAILED
    elif status is SolveStatus.SUCCEEDED and (gap is None or gap <= 1e-6):
        rep.status = ValidationStatus.CERTIFIED
    else:
        # Feasible, fully checked, but not proven optimal. Named differently on purpose.
        rep.status = ValidationStatus.CERTIFIED_INCUMBENT
    return rep
