"""The resolved numeric problem.

Everything the optimiser needs, reduced to arrays and scalars in MW / MWh / INR, with no
remaining reference to user input objects. The independent validator consumes the same
object, which is what makes it independent: it re-derives cost and physics from these
immutable numbers and the exported decisions, never from the solver's own residuals.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..finance import annualised_capex
from ..schemas.common import DT_HOURS, Mode
from ..schemas.domain import ProjectInputs
from ..tariffs import TariffEngine
from .supported import CAP_KEY_BY_TECH, capacity_limits, check_supported


@dataclass
class GenSpec:
    """One buildable generation option, already annualised."""

    key: str
    route: str
    cap_key: str                   # which of the five UI capacity names this option is
    profile: np.ndarray            # capacity factor per block, 0..1
    delivery_factor: float         # 1 - wheeling/transmission loss; 1.0 onsite
    existing_mw: float
    min_mw: float
    max_mw: float
    annuity_inr_per_mw_year: float
    fixed_om_inr_per_mw_year: float
    variable_om_inr_per_mwh: float
    wheeled: bool                  # does the open-access charge stack apply?
    step_mw: float | None = None   # new capacity comes in whole multiples of this


@dataclass
class BatterySpec:
    enabled: bool
    existing_mw: float
    existing_mwh: float
    min_mw: float
    max_mw: float
    min_mwh: float
    max_mwh: float
    annuity_inr_per_mw_year: float
    annuity_inr_per_mwh_year: float
    fixed_om_inr_per_mw_year: float
    eta_charge: float
    eta_discharge: float
    retention_per_block: float
    soc_min_frac: float
    soc_max_frac: float
    max_c_rate: float
    aux_frac_of_power: float
    wear_inr_per_mwh_discharged: float
    warranty_throughput_efc: float
    terminal_rule: str
    initial_soc_frac: float
    allow_grid_charging: bool
    # Warranty cycles spread over the cell life the energy annuity assumes. Cycling
    # faster wears the cells out before that annuity has paid for them.
    max_efc_per_year: float | None = None


@dataclass
class BankingSpec:
    """Wheeled-energy banking, resolved onto the block index.

    Energies are MWh at the site-delivered basis, the same basis as `use`, so a banked
    MWh and a consumed MWh are the same MWh before the licensee's cut.
    """

    charge_frac: float                  # kept by the licensee, in kind, on deposit
    charge_inr_per_mwh: float           # money charge on every MWh deposited
    lapse_credit_inr_per_mwh: float     # paid for what is left at settlement
    period_index: np.ndarray            # settlement period of each block, 0-based
    n_periods: int
    drawal_allowed: np.ndarray          # bool per block
    cap_mwh: np.ndarray | None          # per period, or no cap

    def period_ends(self) -> np.ndarray:
        """Last block of each settlement period, where the balance lapses."""
        p = self.period_index
        return np.flatnonzero(np.r_[p[1:] != p[:-1], True])

    def period_starts(self) -> np.ndarray:
        p = self.period_index
        return np.flatnonzero(np.r_[True, p[1:] != p[:-1]])


@dataclass
class RunSpec:
    """Immutable, hashable, and sufficient on its own to reproduce a run."""

    project_id: str
    operating_year: int
    timezone: str
    n: int
    dt: float
    timestamps_utc: pd.DatetimeIndex
    load_mw: np.ndarray
    grid_available: np.ndarray

    # retail
    energy_inr_per_mwh: np.ndarray
    tariff_rule_id: np.ndarray
    month_index: np.ndarray
    n_months: int
    month_labels: list[str]
    demand_rate_inr_per_mw_month: float
    min_billable_demand_mw: float
    duty_frac: float
    export_inr_per_mwh: float

    # market and open access
    iex_buy_inr_per_mwh: np.ndarray
    iex_sell_inr_per_mwh: np.ndarray
    market_enabled: bool
    market_buy_enabled: bool
    market_sell_enabled: bool
    market_buy_limit_mw: float
    market_sell_limit_mw: float
    market_txn_inr_per_mwh: float
    market_oa_applies: bool
    oa_charge_inr_per_mwh: float

    # site
    import_limit_mw: float
    export_limit_mw: float
    allow_export: bool

    generation: list[GenSpec]
    battery: BatterySpec
    existing_fixed_om_inr_year: float

    mode: Mode = Mode.FIND_OPTIMUM
    fixed_capacities: dict[str, float] | None = None
    model_version: str = ""
    notes: dict = field(default_factory=dict)
    banking: BankingSpec | None = None

    # -- identity ------------------------------------------------------------------
    def fingerprint(self) -> str:
        """Hash of everything except the capacity decision.

        Mode B is only comparable to a Mode A baseline when this matches: capacities are
        the single intended difference, so they are deliberately excluded here and
        compared separately.
        """
        h = hashlib.sha256()
        for arr in (self.load_mw, self.grid_available, self.energy_inr_per_mwh,
                    self.iex_buy_inr_per_mwh, self.iex_sell_inr_per_mwh,
                    self.month_index.astype(np.float64)):
            h.update(np.ascontiguousarray(arr, dtype=np.float64).tobytes())
        for g in self.generation:
            h.update(np.ascontiguousarray(g.profile, dtype=np.float64).tobytes())
            # An unset optional field does not change the problem, so it must not change
            # the fingerprint and orphan runs saved before the field existed.
            h.update(json.dumps({k: v for k, v in g.__dict__.items()
                                 if k != "profile" and v is not None},
                                sort_keys=True, default=str).encode())
        h.update(json.dumps(self.battery.__dict__, sort_keys=True, default=str).encode())
        h.update(json.dumps({
            "project": self.project_id, "year": self.operating_year, "tz": self.timezone,
            "n": self.n, "dt": self.dt,
            "demand_rate": self.demand_rate_inr_per_mw_month,
            "min_billable": self.min_billable_demand_mw, "duty": self.duty_frac,
            "export_rate": self.export_inr_per_mwh, "oa": self.oa_charge_inr_per_mwh,
            "import_limit": self.import_limit_mw, "export_limit": self.export_limit_mw,
            "allow_export": self.allow_export, "market": [
                self.market_enabled, self.market_buy_enabled, self.market_sell_enabled,
                self.market_buy_limit_mw, self.market_sell_limit_mw,
                self.market_txn_inr_per_mwh, self.market_oa_applies],
            "existing_fom": self.existing_fixed_om_inr_year,
            "model_version": self.model_version,
        }, sort_keys=True).encode())
        bk = self.banking
        if bk is not None:              # absent banking leaves older fingerprints intact
            h.update(np.ascontiguousarray(bk.period_index, dtype=np.float64).tobytes())
            h.update(np.ascontiguousarray(bk.drawal_allowed, dtype=np.float64).tobytes())
            if bk.cap_mwh is not None:
                h.update(np.ascontiguousarray(bk.cap_mwh, dtype=np.float64).tobytes())
            h.update(json.dumps({"banking": [bk.charge_frac, bk.charge_inr_per_mwh,
                                             bk.lapse_credit_inr_per_mwh, bk.n_periods,
                                             bk.cap_mwh is None]}).encode())
        return h.hexdigest()[:32]

    @property
    def annual_load_mwh(self) -> float:
        return float(self.load_mw.sum() * self.dt)


def build_spec(inputs: ProjectInputs, frame: pd.DataFrame, operating_year: int,
               *, mode: Mode = Mode.FIND_OPTIMUM,
               fixed_capacities: dict[str, float] | None = None,
               model_version: str = "") -> RunSpec:
    """Resolve user inputs and one aligned frame into the numeric problem."""
    check_supported(inputs, operating_year,
                    fixed_capacities=fixed_capacities if mode is Mode.MANUAL else None)
    ts = pd.DatetimeIndex(frame["timestamp_utc"])
    n = len(frame)
    tz = inputs.project.timezone

    engine = TariffEngine(inputs.tariff, inputs.open_access, timezone=tz)
    res = engine.resolve(ts)

    fin = inputs.finance
    r = fin.discount_rate

    existing_by_tech: dict[str, float] = {}
    existing_bess_mw = existing_bess_mwh = 0.0
    existing_fom = 0.0
    for a in inputs.existing_assets:
        existing_fom += a.capacity_mw * a.fixed_om_inr_per_mw_year
        if a.technology == "bess":
            existing_bess_mw += a.capacity_mw
            existing_bess_mwh += a.energy_mwh
        else:
            existing_by_tech[a.technology] = existing_by_tech.get(a.technology, 0.0) + a.capacity_mw

    limits = capacity_limits(inputs)
    gens: list[GenSpec] = []
    for opt in inputs.asset_options:
        existing_mw = existing_by_tech.get(opt.technology, 0.0)
        lo, hi, step = limits.get(CAP_KEY_BY_TECH[opt.technology], (0.0, 0.0, None))
        # A disabled option still supplies the profile for plant already built with that
        # technology; it just cannot buy more.
        if not opt.enabled and existing_mw <= 0:
            continue
        if opt.profile_key not in frame.columns:
            raise KeyError(f"asset option {opt.option_id!r} needs profile {opt.profile_key!r}")
        # The profile is used undegraded: this is the first operating year, and
        # degradation_pct_per_year compounds from the second, which is out of scope.
        prof = frame[opt.profile_key].to_numpy(dtype=float)
        gens.append(GenSpec(
            key=opt.option_id,
            route=opt.route.value,
            cap_key=CAP_KEY_BY_TECH[opt.technology],
            profile=prof,
            delivery_factor=1.0 - opt.delivery_loss_pct,
            existing_mw=existing_mw,
            min_mw=lo, max_mw=hi,
            annuity_inr_per_mw_year=annualised_capex(opt.capex_inr_per_mw, r, opt.life_years),
            fixed_om_inr_per_mw_year=opt.fixed_om_inr_per_mw_year,
            variable_om_inr_per_mwh=opt.variable_om_inr_per_mwh,
            wheeled=opt.route.value == "open_access",
            step_mw=step,
        ))

    b = inputs.battery
    tech = b.technical
    usable_frac = tech.soc_max_frac - tech.soc_min_frac
    # Wear is the marginal cost of consuming warranty throughput. The cell annuity below
    # recovers the calendar-life replacement; charging both for the same liability is the
    # double count this expression is written to avoid, so wear is priced against
    # throughput only and the validator checks the warranty envelope separately.
    wear = (b.capex_inr_per_mwh / (tech.warranty_throughput_mwh_per_mwh * max(usable_frac, 1e-6))
            + b.variable_om_inr_per_mwh_throughput)
    retention = (1.0 - tech.self_discharge_pct_per_day) ** (DT_HOURS / 24.0)

    bat = BatterySpec(
        enabled=b.enabled,
        existing_mw=existing_bess_mw, existing_mwh=existing_bess_mwh,
        min_mw=b.min_power_mw, max_mw=b.max_power_mw,
        min_mwh=b.min_energy_mwh, max_mwh=b.max_energy_mwh,
        annuity_inr_per_mw_year=annualised_capex(b.capex_inr_per_mw, r, b.power_life_years),
        annuity_inr_per_mwh_year=annualised_capex(b.capex_inr_per_mwh, r, b.energy_life_years),
        fixed_om_inr_per_mw_year=b.fixed_om_inr_per_mw_year,
        eta_charge=tech.eta_charge, eta_discharge=tech.eta_discharge,
        retention_per_block=retention,
        soc_min_frac=tech.soc_min_frac, soc_max_frac=tech.soc_max_frac,
        max_c_rate=tech.max_c_rate, aux_frac_of_power=tech.aux_load_frac_of_power,
        wear_inr_per_mwh_discharged=wear,
        warranty_throughput_efc=tech.warranty_throughput_mwh_per_mwh,
        terminal_rule=tech.terminal_soc_rule, initial_soc_frac=tech.initial_soc_frac,
        allow_grid_charging=tech.allow_grid_charging,
        max_efc_per_year=tech.warranty_throughput_mwh_per_mwh / b.energy_life_years,
    )

    def col(name: str, default: float | None = None) -> np.ndarray:
        if name in frame.columns:
            return frame[name].to_numpy(dtype=float)
        if default is None:
            raise KeyError(f"frame is missing required column {name!r}")
        return np.full(n, default)

    load = col("load_mw")
    oa = inputs.open_access
    banking = None
    if oa.banking_enabled:
        # Monthly settlement uses the same local billing months as the demand charge.
        period = (res.month_index if oa.banking_settlement == "month"
                  else np.zeros(n, dtype=int))
        n_periods = int(period.max()) + 1 if n else 0
        hour = pd.DatetimeIndex(ts).tz_convert(tz).hour.to_numpy()
        cap = None
        if oa.banking_cap_frac_of_load is not None:
            cap = oa.banking_cap_frac_of_load * np.bincount(
                period, weights=load * DT_HOURS, minlength=n_periods)
        banking = BankingSpec(
            charge_frac=oa.banking_charge_frac,
            charge_inr_per_mwh=oa.banking_charge_inr_per_kwh * 1000.0,
            lapse_credit_inr_per_mwh=oa.banking_lapse_credit_inr_per_kwh * 1000.0,
            period_index=np.asarray(period, dtype=int), n_periods=n_periods,
            drawal_allowed=~np.isin(hour, oa.banking_drawal_blocked_hours),
            cap_mwh=cap)

    mk = inputs.market
    return RunSpec(
        project_id=inputs.project.project_id,
        operating_year=operating_year,
        timezone=tz,
        n=n, dt=DT_HOURS, timestamps_utc=ts,
        load_mw=load,
        grid_available=col("grid_available", 1.0),
        energy_inr_per_mwh=res.energy_inr_per_mwh,
        tariff_rule_id=res.rule_id,
        month_index=res.month_index, n_months=res.n_months, month_labels=res.month_labels,
        demand_rate_inr_per_mw_month=res.demand_rate_inr_per_mw_month,
        min_billable_demand_mw=res.min_billable_demand_mw,
        duty_frac=res.duty_frac, export_inr_per_mwh=res.export_inr_per_mwh,
        iex_buy_inr_per_mwh=col("iex_buy_inr_per_kwh", 0.0) * 1000.0,
        iex_sell_inr_per_mwh=col("iex_sell_inr_per_kwh", 0.0) * 1000.0,
        market_enabled=mk.enabled, market_buy_enabled=mk.buy_enabled,
        market_sell_enabled=mk.sell_enabled,
        market_buy_limit_mw=mk.buy_limit_mw, market_sell_limit_mw=mk.sell_limit_mw,
        market_txn_inr_per_mwh=mk.transaction_inr_per_kwh * 1000.0,
        market_oa_applies=mk.oa_charges_apply,
        oa_charge_inr_per_mwh=engine.oa_charge_inr_per_mwh,
        import_limit_mw=inputs.project.site.import_limit_mw,
        export_limit_mw=inputs.project.site.export_limit_mw,
        allow_export=inputs.project.site.export_limit_mw > 0 and tech.allow_export,
        generation=gens, battery=bat,
        existing_fixed_om_inr_year=existing_fom,
        mode=mode, fixed_capacities=fixed_capacities, model_version=model_version,
        banking=banking,
    )
