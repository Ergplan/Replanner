"""The lifetime of a design: what it costs, year by year, over the study period.

The optimiser plans one operating year, with capital as an annuity. A client also asks
what the design costs over its life, when it pays back and at what return. Answering that
honestly means running the plant through its years:

  * panels lose output at each technology's own degradation rate;
  * battery cells fade at the calendar rate and come back to full when they are replaced
    at the end of their life;
  * tariffs, prices, charges and O&M escalate at the finance escalation rate;
  * capital is a cash flow: bought at year 0, replaced at the end of its life, and
    credited with straight-line salvage at the end of the study.

Each evaluated year is a dispatch solve at fixed capacities, independently certified like
any other run. Solving all twenty years is slow, so a sample is solved: the first and
last years, every fifth, and the years either side of each replacement, where costs step
rather than drift. Operating costs between sampled years are interpolated, and the result
says which years are which. The same is done for grid supply alone, with no new capacity,
which is what the savings, payback and return are measured against.

This is an evaluation of a fixed design, not an optimisation over the years: sizing
jointly across a multi-year horizon remains unsupported.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .finance import salvage_fraction
from .optimization import build_spec, solve_year
from .optimization.spec import RunSpec
from .optimization.supported import CAP_KEY_BY_TECH, CAP_KEYS
from .schemas.common import Mode
from .schemas.domain import ProjectInputs
from .validation import certify

# Ledger lines that are not operating costs: capital is handled as cash flows here.
_CAPITAL_LINES = ("annualised_capex_new",)


@dataclass
class YearResult:
    year: int
    sampled: bool
    opex_design: float | None
    opex_grid_only: float | None
    validation: str | None = None
    grid_only_validation: str | None = None
    note: str = ""


@dataclass
class LifetimeResult:
    study_years: int
    discount_rate: float
    escalation: float
    basis: str
    design: dict[str, float]
    sampled_years: list[int]
    years: list[YearResult]
    capex_year0: float
    replacements: list[dict]
    salvage: float
    annual_kwh: float
    npv_design: float | None = None
    npv_grid_only: float | None = None
    levelised_design: float | None = None        # INR per kWh consumed, over the life
    levelised_grid_only: float | None = None
    payback_year: int | None = None
    irr: float | None = None
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "years"}
        d["years"] = [y.__dict__ for y in self.years]
        return d


# ---- pieces that need no solver -------------------------------------------------------
def capital_items(inputs: ProjectInputs, design: dict[str, float]) -> list[dict]:
    """Every capital item the design buys, with its cost and life."""
    items = []
    for o in inputs.asset_options:
        key = CAP_KEY_BY_TECH.get(o.technology)
        mw = float(design.get(key, 0.0)) if o.enabled and key else 0.0
        if mw > 0:
            items.append({"item": o.option_id, "capex": mw * o.capex_inr_per_mw,
                          "life": o.life_years})
    b = inputs.battery
    if b.enabled:
        p, e = float(design.get("bess_power_mw", 0.0)), float(design.get("bess_energy_mwh", 0.0))
        if p > 0:
            items.append({"item": "bess_power", "capex": p * b.capex_inr_per_mw,
                          "life": b.power_life_years})
        if e > 0:
            items.append({"item": "bess_energy", "capex": e * b.capex_inr_per_mwh,
                          "life": b.energy_life_years})
    return items


def capital_schedule(inputs: ProjectInputs, design: dict[str, float], study_years: int,
                     escalation: float = 0.0) -> tuple[float, list[dict], float]:
    """Year-0 purchase, replacements within the study, and salvage at its end.

    An item bought at year 0 serves years 1..L; its replacement, bought at year L, serves
    L+1..2L. Replacements are priced at today's cost escalated to the year they are made.
    Salvage is the unconsumed share of the last purchase, credited in the final year.
    """
    items = capital_items(inputs, design)
    capex0 = sum(i["capex"] for i in items)
    repl, salvage = [], 0.0
    conv = inputs.finance.salvage
    for i in items:
        t = i["life"]
        last_cost = i["capex"]
        while t < study_years:
            cost = i["capex"] * (1 + escalation) ** t
            repl.append({"year": t, "item": i["item"], "inr": cost})
            last_cost = cost
            t += i["life"]
        salvage += last_cost * salvage_fraction(i["life"], study_years, conv)
    return capex0, sorted(repl, key=lambda r: r["year"]), salvage


def sample_years(inputs: ProjectInputs, study_years: int, every: int = 5) -> list[int]:
    """Years to solve: first, last, every `every`th, and either side of a replacement."""
    ys = {1, study_years, *range(every, study_years + 1, every)}
    lives = [o.life_years for o in inputs.asset_options if o.enabled]
    if inputs.battery.enabled:
        lives += [inputs.battery.power_life_years, inputs.battery.energy_life_years]
    for life in lives:
        for t in range(life, study_years, life):
            ys |= {t, t + 1}
    return sorted(y for y in ys if 1 <= y <= study_years)


def year_spec(base: RunSpec, inputs: ProjectInputs, design: dict[str, float], k: int,
              escalation: float) -> RunSpec:
    """The operating year `k` (1-based) of the design, from the year-1 problem."""
    s = copy.deepcopy(base)
    esc = (1 + escalation) ** (k - 1)
    degr = {o.option_id: o.degradation_pct_per_year for o in inputs.asset_options}
    for g in s.generation:
        g.profile = g.profile * (1 - degr.get(g.key, 0.0)) ** (k - 1)
        g.fixed_om_inr_per_mw_year *= esc
        g.variable_om_inr_per_mwh *= esc
    # Cells fade by the calendar and are new again the year after replacement.
    tech = inputs.battery.technical
    life = inputs.battery.energy_life_years
    fade = (1 - tech.calendar_fade_pct_per_year) ** ((k - 1) % life)
    b = s.battery
    b.existing_mwh *= fade
    b.fixed_om_inr_per_mw_year *= esc
    b.wear_inr_per_mwh_discharged *= esc
    fixed = {key: float(design.get(key, 0.0)) for key in CAP_KEYS}
    fixed["bess_energy_mwh"] *= fade
    s.energy_inr_per_mwh = s.energy_inr_per_mwh * esc
    s.demand_rate_inr_per_mw_month *= esc
    s.export_inr_per_mwh *= esc
    s.iex_buy_inr_per_mwh = s.iex_buy_inr_per_mwh * esc
    s.iex_sell_inr_per_mwh = s.iex_sell_inr_per_mwh * esc
    s.market_txn_inr_per_mwh *= esc
    s.oa_charge_inr_per_mwh *= esc
    s.existing_fixed_om_inr_year *= esc
    if s.banking is not None:
        s.banking.charge_inr_per_mwh *= esc
        s.banking.lapse_credit_inr_per_mwh *= esc
    s.mode, s.fixed_capacities = Mode.MANUAL, fixed
    return s


def irr(flows: np.ndarray) -> float | None:
    """Internal rate of return of an incremental cash-flow series (year 0 first), by
    bisection. None when the flows never change sign, so no rate exists."""
    flows = np.asarray(flows, dtype=float)
    if not (flows.min() < 0 < flows.max()):
        return None
    f = lambda r: float(np.sum(flows / (1 + r) ** np.arange(flows.size)))
    lo, hi = -0.99, 10.0
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def interpolate(sampled: dict[int, float], study_years: int) -> np.ndarray:
    """Operating cost for every year, straight between the solved ones."""
    xs = sorted(sampled)
    return np.interp(np.arange(1, study_years + 1), xs, [sampled[x] for x in xs])


def finish(res: LifetimeResult, inputs: ProjectInputs) -> LifetimeResult:
    """NPVs, levelised costs, payback and return, once operating costs are known."""
    n, r = res.study_years, res.discount_rate
    if any(y.opex_design is None for y in res.years if y.sampled):
        return res
    design_sampled = {y.year: y.opex_design for y in res.years if y.sampled}
    opex_d = interpolate(design_sampled, n)
    flows_d = np.zeros(n + 1)
    flows_d[0] = res.capex_year0
    flows_d[1:] += opex_d
    for rp in res.replacements:
        flows_d[rp["year"]] += rp["inr"]
    flows_d[n] -= res.salvage
    disc = (1 + r) ** -np.arange(n + 1)
    kwh = np.r_[0.0, np.full(n, res.annual_kwh)]
    res.npv_design = float(np.sum(flows_d * disc))
    res.levelised_design = res.npv_design / float(np.sum(kwh * disc)) if res.annual_kwh else None
    for y in res.years:
        if not y.sampled:
            y.opex_design = float(opex_d[y.year - 1])

    grid_sampled = {y.year: y.opex_grid_only for y in res.years
                    if y.sampled and y.opex_grid_only is not None}
    if len(grid_sampled) == sum(1 for y in res.years if y.sampled):
        opex_g = interpolate(grid_sampled, n)
        flows_g = np.r_[0.0, opex_g]
        res.npv_grid_only = float(np.sum(flows_g * disc))
        res.levelised_grid_only = (res.npv_grid_only / float(np.sum(kwh * disc))
                                   if res.annual_kwh else None)
        saving = flows_g - flows_d                   # positive when the design is cheaper
        res.irr = irr(saving)
        cum = np.cumsum(saving)
        paid = np.flatnonzero(cum >= 0)
        res.payback_year = int(paid[0]) if paid.size and paid[0] > 0 else None
        for y in res.years:
            if not y.sampled:
                y.opex_grid_only = float(opex_g[y.year - 1])
    return res


# ---- the evaluation ----------------------------------------------------------------------
def _opex(spec: RunSpec, horizon_blocks: int | None, time_limit_s: float) -> tuple[float | None, str]:
    art = solve_year(spec, time_limit_s=time_limit_s)
    if art.dispatch.empty:
        return None, f"not feasible ({art.outcome.termination})"
    rep = certify(spec, art.capacities, art.dispatch, art.ledger,
                  solver_objective=art.outcome.objective, status=art.outcome.status,
                  gap=art.outcome.gap, horizon_blocks=horizon_blocks)
    if not rep.status.value.startswith("certified"):
        return None, f"{rep.status.value}: " + "; ".join(i.check for i in rep.material_issues[:3])
    capital = sum(getattr(art.ledger, k) for k in _CAPITAL_LINES)
    return art.ledger.total_annual_cost - capital, rep.status.value


def evaluate(inputs: ProjectInputs, frame, design: dict[str, float], *, year: int,
             model_version: str = "", every: int = 5, horizon_blocks: int | None = None,
             time_limit_s: float = 900.0, grid_only: bool = True,
             progress: Callable[[str], None] | None = None) -> LifetimeResult:
    fin = inputs.finance
    n = fin.study_period_years
    base = build_spec(inputs, frame, year, model_version=model_version)
    capex0, repl, salvage = capital_schedule(inputs, design, n, fin.opex_escalation)
    years = sample_years(inputs, n, every)
    res = LifetimeResult(
        study_years=n, discount_rate=fin.discount_rate, escalation=fin.opex_escalation,
        basis=fin.basis, design={k: float(design.get(k, 0.0)) for k in CAP_KEYS},
        sampled_years=years, years=[], capex_year0=capex0, replacements=repl,
        salvage=salvage, annual_kwh=float(base.load_mw.sum() * base.dt * 1000))
    zero = {k: 0.0 for k in CAP_KEYS}
    stop_grid = not grid_only
    for k in range(1, n + 1):
        if k not in years:
            res.years.append(YearResult(k, False, None, None))
            continue
        if progress:
            progress(f"year {k} of {n}")
        od, vd = _opex(year_spec(base, inputs, design, k, fin.opex_escalation),
                       horizon_blocks, time_limit_s)
        og = vg = None
        if not stop_grid:
            og, vg = _opex(year_spec(base, inputs, zero, k, fin.opex_escalation),
                           horizon_blocks, time_limit_s)
            if og is None:
                stop_grid = True
                res.problems.append(f"grid supply alone cannot serve the site in year {k} "
                                    f"({vg}), so there is no grid-only comparison")
        yr = YearResult(k, True, od, og, vd, vg)
        if od is None:
            yr.note = "the design cannot serve the site this year"
            res.problems.append(f"year {k}: the design cannot serve the site ({vd}). A battery "
                                "sized for year one fades; size for end-of-life capacity or "
                                "plan augmentation.")
        res.years.append(yr)
        if od is None:
            break
    return finish(res, inputs)
