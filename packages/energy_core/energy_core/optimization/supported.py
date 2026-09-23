"""Refuse inputs the model would otherwise solve as if they were not there.

The schema carries fields for features this release does not model: multi-year staging,
wheeled-energy banking, partial-year plant. A field that parses but is then ignored is
the worst kind of wrong answer, because the result looks complete. Everything here is
checked before a model is built, and every problem is reported at once so a user fixing
an input file does not discover them one solve at a time.
"""
from __future__ import annotations

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
        out.append("open_access.banking_enabled: banking without an intertemporal balance, "
                   "expiry and settlement rules would be unlimited free storage")

    techs: dict[str, list[str]] = {}
    for opt in inputs.asset_options:
        if opt.technology not in CAP_KEY_BY_TECH:
            out.append(f"asset option {opt.option_id!r}: unknown technology "
                       f"{opt.technology!r}; expected one of {sorted(CAP_KEY_BY_TECH)}")
            continue
        techs.setdefault(opt.technology, []).append(opt.option_id)
        if opt.step_mw is not None and opt.enabled:
            out.append(f"asset option {opt.option_id!r}: step_mw (discrete sizes) is not "
                       "supported")
    for tech, ids in techs.items():
        if len(ids) > 1:
            # Capacities are reported per technology, so two options of one technology
            # would overwrite each other's result and count existing plant twice.
            out.append(f"asset options {ids} share technology {tech!r}; one option per "
                       "technology")

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
        buildable = {CAP_KEY_BY_TECH[o.technology] for o in inputs.asset_options
                     if o.enabled and o.technology in CAP_KEY_BY_TECH}
        if inputs.battery.enabled:
            buildable |= {"bess_power_mw", "bess_energy_mwh"}
        for k, v in fixed_capacities.items():
            if k in CAP_KEYS and k not in buildable and float(v) > 0:
                out.append(f"capacity {k} = {v}: no enabled option can build it")
    return out


def check_supported(inputs: ProjectInputs, operating_year: int, *,
                    fixed_capacities: dict[str, float] | None = None) -> None:
    problems = unsupported_input_problems(inputs, operating_year,
                                          fixed_capacities=fixed_capacities)
    if problems:
        raise UnsupportedInput(problems)
