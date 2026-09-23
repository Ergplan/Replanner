"""Refuse inputs the model would otherwise solve as if they were not there.

The schema carries fields for features this release does not model: multi-year staging,
wheeled-energy banking, partial-year plant. A field that parses but is then ignored is
the worst kind of wrong answer, because the result looks complete. Everything here is
checked before a model is built, and every problem is reported at once so a user fixing
an input file does not discover them one solve at a time.
"""
from __future__ import annotations

import math
from datetime import date

from ..schemas.domain import ProjectInputs

#: The five capacity names the wizard, the sliders and the Mode B comparison all speak.
CAP_KEYS = ("solar_onsite_mw", "solar_remote_mw", "wind_remote_mw",
            "bess_power_mw", "bess_energy_mwh")
CAP_KEY_BY_TECH = {
    "solar_onsite": "solar_onsite_mw",
    "solar_remote": "solar_remote_mw",
    "wind_remote": "wind_remote_mw",
}


#: Absolute slack on capacity bounds and on the step grid, MW. Solver output lands a
#: rounding error either side of an integer; a real off-grid size is orders larger.
CAP_TOL_MW = 1e-6


def unit_range(min_mw: float, max_mw: float, step_mw: float) -> tuple[int, int]:
    """The whole numbers of units whose total lies within [min_mw, max_mw]."""
    lo = math.ceil(min_mw / step_mw - 1e-9)
    hi = math.floor(max_mw / step_mw + 1e-9)
    return lo, hi


def off_grid_mw(value_mw: float, step_mw: float) -> float:
    """Distance in MW from value_mw to the nearest whole number of steps."""
    return abs(value_mw - round(value_mw / step_mw) * step_mw)


def _existing_mw(inputs: ProjectInputs) -> dict[str, float]:
    out: dict[str, float] = {}
    for a in inputs.existing_assets:
        out[a.technology] = out.get(a.technology, 0.0) + a.capacity_mw
    return out


def capacity_limits(inputs: ProjectInputs) -> dict[str, tuple[float, float, float | None]]:
    """(min, max, step) of new capacity for every capacity Mode A may buy.

    The option's own max_mw is not the whole story for onsite solar: the roof and ground
    it goes on hold a fixed MWp, and panels already there occupy part of it. The model,
    the manual-scenario check, the certifier and the sliders all read this one answer.
    """
    site = inputs.project.site
    room = max(site.roof_area_mw_cap + site.land_mw_cap
               - _existing_mw(inputs).get("solar_onsite", 0.0), 0.0)
    out: dict[str, tuple[float, float, float | None]] = {}
    for o in inputs.asset_options:
        if not o.enabled or o.technology not in CAP_KEY_BY_TECH:
            continue
        hi = min(o.max_mw, room) if o.technology == "solar_onsite" else o.max_mw
        out[CAP_KEY_BY_TECH[o.technology]] = (o.min_mw, hi, o.step_mw)
    b = inputs.battery
    if b.enabled:
        out["bess_power_mw"] = (b.min_power_mw, b.max_power_mw, None)
        out["bess_energy_mwh"] = (b.min_energy_mwh, b.max_energy_mwh, None)
    return out


class UnsupportedInput(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("inputs this release cannot model faithfully:\n  - "
                         + "\n  - ".join(problems))


def unsupported_input_problems(inputs: ProjectInputs, operating_year: int, *,
                               fixed_capacities: dict[str, float] | None = None) -> list[str]:
    out: list[str] = []
    proj = inputs.project

    years = sorted(set(proj.operating_years))
    if len(years) > 1:
        out.append(f"operating_years {years}: multi-year horizons are not supported; "
                   "solve one operating year")
    if operating_year not in years:
        out.append(f"operating year {operating_year} is not in project.operating_years {years}")
    if inputs.expansion:
        ids = [p.phase_id for p in inputs.expansion]
        out.append(f"expansion phases {ids}: staged load growth is not supported")
    if inputs.open_access.banking_enabled:
        existing = _existing_mw(inputs)
        wheeled = [o.option_id for o in inputs.asset_options
                   if o.route.value == "open_access"
                   and (o.enabled or existing.get(o.technology, 0.0) > 0)]
        if not wheeled:
            out.append("open_access.banking_enabled: nothing is wheeled, so there is "
                       "nothing to bank; enable an open-access option or disable banking")

    techs: dict[str, list[str]] = {}
    for opt in inputs.asset_options:
        if opt.technology not in CAP_KEY_BY_TECH:
            out.append(f"asset option {opt.option_id!r}: unknown technology "
                       f"{opt.technology!r}; expected one of {sorted(CAP_KEY_BY_TECH)}")
            continue
        techs.setdefault(opt.technology, []).append(opt.option_id)
    for tech, ids in techs.items():
        if len(ids) > 1:
            # Capacities are reported per technology, so two options of one technology
            # would overwrite each other's result and count existing plant twice.
            out.append(f"asset options {ids} share technology {tech!r}; one option per "
                       "technology")

    site = proj.site
    site_mwp = site.roof_area_mw_cap + site.land_mw_cap
    onsite_existing = _existing_mw(inputs).get("solar_onsite", 0.0)
    if onsite_existing > site_mwp + CAP_TOL_MW:
        out.append(f"existing onsite solar {onsite_existing} MWp exceeds the site's roof + "
                   f"land limit of {site_mwp} MWp")
    limits = capacity_limits(inputs)
    for opt in inputs.asset_options:
        key = CAP_KEY_BY_TECH.get(opt.technology)
        if not opt.enabled or key not in limits:
            continue
        lo, hi, step = limits[key]
        if opt.technology == "solar_onsite" and opt.max_mw > 0 and hi <= 0:
            out.append(f"asset option {opt.option_id!r} may build {opt.max_mw} MWp, but "
                       f"site roof + land ({site_mwp} MWp) less existing onsite solar leaves "
                       "no room; set site.roof_area_mw_cap / land_mw_cap or disable it")
        elif lo > hi + CAP_TOL_MW:
            out.append(f"asset option {opt.option_id!r}: min_mw {lo} exceeds the most it may "
                       f"build, {hi}")
        elif step is not None:
            k_lo, k_hi = unit_range(lo, hi, step)
            if k_lo > k_hi:
                out.append(f"asset option {opt.option_id!r}: no multiple of step_mw {step} "
                           f"lies between {lo} and {hi}")

    year_start, year_end = date(operating_year, 1, 1), date(operating_year, 12, 31)
    for a in inputs.existing_assets:
        if a.technology != "bess" and a.technology not in techs:
            out.append(f"existing asset {a.asset_id!r}: no asset option of technology "
                       f"{a.technology!r} supplies its generation profile, so its energy "
                       "would be dropped while its O&M is still charged")
        if a.commissioned > year_start:
            out.append(f"existing asset {a.asset_id!r}: commissioned {a.commissioned}, after "
                       f"the operating year starts; partial-year availability is not supported")
        if a.retires is not None and a.retires <= year_end:
            out.append(f"existing asset {a.asset_id!r}: retires {a.retires}, within or before "
                       f"operating year {operating_year}; retirement dates are not supported")

    if fixed_capacities is not None:
        unknown = sorted(set(fixed_capacities) - set(CAP_KEYS))
        if unknown:
            out.append(f"unknown capacity keys {unknown}; expected a subset of {list(CAP_KEYS)}")
        for k, v in fixed_capacities.items():
            if k in CAP_KEYS and not float(v) >= 0:
                out.append(f"capacity {k} = {v}: must be a non-negative number")
        buildable = set(capacity_limits(inputs))
        for k, v in fixed_capacities.items():
            if k in CAP_KEYS and k not in buildable and float(v) > 0:
                out.append(f"capacity {k} = {v}: no enabled option can build it")
        out += _manual_outside_options(inputs, fixed_capacities)
    return out


def _manual_outside_options(inputs: ProjectInputs,
                            fixed_capacities: dict[str, float]) -> list[str]:
    """A manual scenario must be one Mode A was allowed to choose.

    Otherwise it can undercut the certified optimum, and the premium that is the whole
    point of Mode B turns negative and means nothing. A key left out is fixed at zero by
    the model, so it is checked at zero here.
    """
    out: list[str] = []
    for key, (lo, hi, step) in capacity_limits(inputs).items():
        v = float(fixed_capacities.get(key, 0.0))
        if math.isnan(v):                       # already reported as not a number
            continue
        if v < lo - CAP_TOL_MW or v > hi + CAP_TOL_MW:
            out.append(f"capacity {key} = {v}: outside the option's range [{lo}, {hi}]")
        elif step is not None and off_grid_mw(v, step) > CAP_TOL_MW:
            out.append(f"capacity {key} = {v}: not a whole multiple of step_mw {step}")
    return out


def check_supported(inputs: ProjectInputs, operating_year: int, *,
                    fixed_capacities: dict[str, float] | None = None) -> None:
    problems = unsupported_input_problems(inputs, operating_year,
                                          fixed_capacities=fixed_capacities)
    if problems:
        raise UnsupportedInput(problems)
