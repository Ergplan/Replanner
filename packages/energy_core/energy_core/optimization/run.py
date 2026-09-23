"""Build, solve, extract, and refine.

The refinement loop is the interesting part. The model carries mode binaries only on the
blocks where prices could reward a physically impossible flow. After each solve the
exported decisions are scanned across every block; anything found is added to the binary
set and the model is re-solved. The loop is bounded, and the independent validator is the
final word — so the optimisation is fast in the common case without the correctness of
the answer resting on the heuristic that made it fast.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pyomo.environ as pyo

from ..schemas.common import DT_HOURS, Mode, SolveStatus
from ..schemas.results import CapacityResult, CostLedger
from .model import build_model, candidate_simultaneity_blocks
from .solver import SolveOutcome, solve
from .spec import RunSpec

SIMUL_TOL_MW = 1e-6


@dataclass
class RunArtifacts:
    spec: RunSpec
    outcome: SolveOutcome
    capacities: CapacityResult
    ledger: CostLedger
    dispatch: pd.DataFrame
    monthly: pd.DataFrame
    refinement_rounds: int = 0
    binary_blocks: int = 0
    notes: dict = field(default_factory=dict)


def _extract(model, spec: RunSpec) -> tuple[CapacityResult, pd.DataFrame, np.ndarray]:
    n = spec.n
    g = lambda v: np.fromiter((pyo.value(v[t]) for t in range(n)), dtype=float, count=n)

    imp_u, imp_x = g(model.imp_u), g(model.imp_x)
    exp_u, exp_x = g(model.exp_u), g(model.exp_x)
    ch, dis, soc_end = g(model.ch), g(model.dis), g(model.soc)
    soc_start = np.roll(soc_end, 1)                    # the year is cyclic by construction

    caps = CapacityResult(
        bess_power_mw=float(pyo.value(model.bp)),
        bess_energy_mwh=float(pyo.value(model.be)),
        existing_bess_power_mw=spec.battery.existing_mw,
        existing_bess_energy_mwh=spec.battery.existing_mwh,
    )
    df = pd.DataFrame({
        "timestamp_utc": spec.timestamps_utc,
        "duration_hours": DT_HOURS,
        "load_mw": spec.load_mw,
        "tariff_rule_id": spec.tariff_rule_id,
        "tariff_inr_per_mwh": spec.energy_inr_per_mwh,
        "month_index": spec.month_index,
        "import_utility_mw": imp_u,
        "import_market_mw": imp_x,
        "export_utility_mw": exp_u,
        "export_market_mw": exp_x,
        "battery_charge_mw": ch,
        "battery_discharge_mw": dis,
        "soc_start_mwh": soc_start,
        "soc_end_mwh": soc_end,
    })

    total_use = np.zeros(n)
    for gs in spec.generation:
        use = g(lambda t, k=gs.key: model.use[k, t]) if False else np.fromiter(
            (pyo.value(model.use[gs.key, t]) for t in range(n)), dtype=float, count=n)
        curt = np.fromiter((pyo.value(model.curt[gs.key, t]) for t in range(n)),
                           dtype=float, count=n)
        cap_new = float(pyo.value(model.cap[gs.key]))
        setattr(caps, gs.cap_key, cap_new)
        if gs.cap_key == "solar_onsite_mw":
            caps.existing_solar_onsite_mw = gs.existing_mw
        df[f"use_{gs.key}_mw"] = use
        df[f"curtail_{gs.key}_mw"] = curt
        df[f"available_{gs.key}_mw"] = (cap_new + gs.existing_mw) * gs.profile * gs.delivery_factor
        total_use += use

    bp_tot = caps.bess_power_mw + caps.existing_bess_power_mw
    df["aux_mw"] = spec.battery.aux_frac_of_power * bp_tot
    df["renewable_used_mw"] = total_use
    df["unserved_load_mw"] = np.fromiter(
        (pyo.value(model.unserved[t]) for t in range(n)), dtype=float, count=n)
    df["served_load_mw"] = spec.load_mw - df["unserved_load_mw"]
    dem = np.fromiter((pyo.value(model.dem[m]) for m in range(spec.n_months)),
                      dtype=float, count=spec.n_months)
    return caps, df, dem


def _ledger(spec: RunSpec, caps: CapacityResult, df: pd.DataFrame,
            dem: np.ndarray) -> tuple[CostLedger, pd.DataFrame]:
    """Recompute every cost component from the extracted dispatch.

    This is the reporting path, not the certification path: the validator does the same
    arithmetic again from the spec alone and reconciles both against the solver objective.
    """
    dt = spec.dt
    duty = 1.0 + spec.duty_frac
    L = CostLedger()

    for gs in spec.generation:
        cap_new = getattr(caps, gs.cap_key)
        L.annualised_capex_new += cap_new * gs.annuity_inr_per_mw_year
        L.fixed_om += cap_new * gs.fixed_om_inr_per_mw_year
        use = df[f"use_{gs.key}_mw"].to_numpy()
        L.variable_om += float(use.sum() * dt * gs.variable_om_inr_per_mwh)
        if gs.wheeled:
            L.open_access_charges += float(use.sum() * dt * spec.oa_charge_inr_per_mwh)

    b = spec.battery
    L.annualised_capex_new += (caps.bess_power_mw * b.annuity_inr_per_mw_year
                               + caps.bess_energy_mwh * b.annuity_inr_per_mwh_year)
    L.fixed_om += caps.bess_power_mw * b.fixed_om_inr_per_mw_year
    L.fixed_om += spec.existing_fixed_om_inr_year

    imp_u = df["import_utility_mw"].to_numpy()
    imp_x = df["import_market_mw"].to_numpy()
    dis = df["battery_discharge_mw"].to_numpy()

    energy_ex_duty = float((imp_u * dt * spec.energy_inr_per_mwh).sum())
    demand_ex_duty = float(dem.sum() * spec.demand_rate_inr_per_mw_month)
    L.utility_energy = energy_ex_duty
    L.utility_demand = demand_ex_duty
    L.duty = (energy_ex_duty + demand_ex_duty) * spec.duty_frac

    L.market_purchase = float((imp_x * dt * spec.iex_buy_inr_per_mwh).sum())
    L.market_transaction = float(imp_x.sum() * dt * spec.market_txn_inr_per_mwh)
    if spec.market_oa_applies:
        L.open_access_charges += float(imp_x.sum() * dt * spec.oa_charge_inr_per_mwh)
    L.battery_wear = float(dis.sum() * dt * b.wear_inr_per_mwh_discharged)
    L.export_revenue = -(float((df["export_utility_mw"].to_numpy() * dt
                                * spec.export_inr_per_mwh).sum())
                         + float((df["export_market_mw"].to_numpy() * dt
                                  * spec.iex_sell_inr_per_mwh).sum()))
    L.total_annual_cost = L.recompute_total()

    monthly = pd.DataFrame({
        "month": spec.month_labels,
        "billing_demand_mw": dem,
        "demand_charge_inr": dem * spec.demand_rate_inr_per_mw_month * duty,
        "utility_energy_mwh": [float(imp_u[spec.month_index == m].sum() * dt)
                               for m in range(spec.n_months)],
        "utility_energy_inr": [float((imp_u * dt * spec.energy_inr_per_mwh)[
            spec.month_index == m].sum() * duty) for m in range(spec.n_months)],
        "market_energy_mwh": [float(imp_x[spec.month_index == m].sum() * dt)
                              for m in range(spec.n_months)],
    })
    return L, monthly


def _simultaneity_violations(df: pd.DataFrame) -> list[int]:
    ch = df["battery_charge_mw"].to_numpy()
    dis = df["battery_discharge_mw"].to_numpy()
    imp = df["import_utility_mw"].to_numpy() + df["import_market_mw"].to_numpy()
    exp = df["export_utility_mw"].to_numpy() + df["export_market_mw"].to_numpy()
    bad = ((ch > SIMUL_TOL_MW) & (dis > SIMUL_TOL_MW)) | ((imp > SIMUL_TOL_MW) & (exp > SIMUL_TOL_MW))
    return np.flatnonzero(bad).tolist()


def _windows(idx: np.ndarray, spec: RunSpec, limit: int = 6) -> list[dict]:
    """Group consecutive shortfall blocks into readable periods."""
    out, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i != prev + 1:
            out.append((start, prev)); start = i
        prev = i
    out.append((start, prev))
    return [{"from_utc": str(spec.timestamps_utc[int(a)]),
             "to_utc": str(spec.timestamps_utc[int(b)]),
             "blocks": int(b - a + 1),
             "hours": float((b - a + 1) * spec.dt)} for a, b in out[:limit]]


def solve_year(spec: RunSpec, *, time_limit_s: float | None = 900.0, mip_gap: float = 1e-4,
               max_refinements: int = 3, stream: bool = False,
               diagnostic: bool = False) -> RunArtifacts:
    forced: list[int] = []
    rounds = 0
    t0 = time.perf_counter()
    while True:
        model = build_model(spec, force_binary_blocks=forced, diagnostic=diagnostic)
        outcome = solve(model, time_limit_s=time_limit_s, mip_gap=mip_gap, stream=stream)
        if outcome.status not in (SolveStatus.SUCCEEDED, SolveStatus.TIME_LIMIT):
            empty = pd.DataFrame()
            return RunArtifacts(spec, outcome, CapacityResult(), CostLedger(), empty, empty,
                                rounds, len(forced),
                                notes={"reason": outcome.message or outcome.termination})
        caps, df, dem = _extract(model, spec)
        bad = _simultaneity_violations(df)
        if not bad or rounds >= max_refinements:
            ledger, monthly = _ledger(spec, caps, df, dem)
            uns = df["unserved_load_mw"].to_numpy()
            short = np.flatnonzero(uns > 1e-6)
            notes = {
                "diagnostic": diagnostic,
                "unserved_blocks": int(short.size),
                "unserved_mwh": float(uns.sum() * spec.dt),
                "worst_unserved_mw": float(uns.max()) if uns.size else 0.0,
                "first_unserved_utc": (str(spec.timestamps_utc[int(short[0])])
                                       if short.size else None),
                "unserved_windows": _windows(short, spec) if short.size else [],
                "candidate_binary_blocks": len(candidate_simultaneity_blocks(spec)),
                "unresolved_simultaneity_blocks": bad,
                "total_wall_seconds": time.perf_counter() - t0,
            }
            return RunArtifacts(spec, outcome, caps, ledger, df, monthly, rounds,
                                model._info.n_binary_blocks, notes)
        forced = sorted(set(forced) | set(bad))
        rounds += 1
