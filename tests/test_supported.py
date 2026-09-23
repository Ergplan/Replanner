"""Inputs the model does not handle must be refused, never solved as if absent."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from energy_core.ingestion import build_index
from energy_core.optimization import (
    UnsupportedInput, build_spec, capacity_limits, unsupported_input_problems,
)
from energy_core.schemas.common import Mode
from energy_core.schemas.domain import ExistingAsset, ExpansionPhase
from project import seeded_project

YEAR = 2026


def _problems(inputs, **kw):
    return unsupported_input_problems(inputs, YEAR, **kw)


def _day_frame() -> pd.DataFrame:
    ts = build_index(YEAR)[:96]
    return pd.DataFrame({"timestamp_utc": ts, "load_mw": 5.0, "solar_onsite_cf": 0.5,
                         "solar_remote_cf": 0.5, "wind_remote_cf": 0.5})


def test_the_seeded_project_is_supported():
    assert _problems(seeded_project(YEAR)) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda p: setattr(p.project, "operating_years", [2026, 2027]), "multi-year"),
    (lambda p: setattr(p.project, "operating_years", [2027]), "not in project.operating_years"),
    (lambda p: p.expansion.append(ExpansionPhase(phase_id="ph2", commissioning=date(2026, 6, 1),
                                                 load_scale_factor=1.2)), "expansion"),
    (lambda p: setattr(p.open_access, "banking_enabled", True), "banking"),
    (lambda p: setattr(p.existing_assets[0], "retires", date(2026, 9, 30)), "retires"),
    (lambda p: setattr(p.existing_assets[0], "commissioned", date(2026, 3, 1)), "commissioned"),
    (lambda p: setattr(p.asset_options[2], "technology", "solar_onsite"), "share technology"),
    (lambda p: p.existing_assets.append(ExistingAsset(
        asset_id="old-diesel", technology="diesel", capacity_mw=2.0,
        commissioned=date(2015, 1, 1))), "no asset option"),
])
def test_unsupported_inputs_are_named(mutate, needle):
    p = seeded_project(YEAR)
    mutate(p)
    problems = _problems(p)
    assert any(needle in s for s in problems), problems


def test_a_retirement_after_the_year_is_harmless():
    p = seeded_project(YEAR)
    p.existing_assets[0].retires = date(2040, 3, 31)
    assert _problems(p) == []


def test_every_problem_is_reported_at_once():
    p = seeded_project(YEAR)
    p.open_access.banking_enabled = True
    p.project.operating_years = [2026, 2027]
    assert len(_problems(p)) == 2


@pytest.mark.parametrize("caps,needle", [
    ({"solar_oa_mw": 10.0}, "unknown capacity keys"),
    ({"bess_power_mw": -1.0}, "non-negative"),
    ({"bess_power_mw": float("nan")}, "non-negative"),
])
def test_a_manual_scenario_cannot_smuggle_in_bad_capacities(caps, needle):
    assert any(needle in s for s in _problems(seeded_project(YEAR), fixed_capacities=caps))


def test_a_manual_scenario_cannot_build_a_disabled_option():
    p = seeded_project(YEAR)
    p.asset_options[2].enabled = False
    assert any("no enabled option" in s
               for s in _problems(p, fixed_capacities={"wind_remote_mw": 5.0}))
    assert _problems(p, fixed_capacities={"wind_remote_mw": 0.0}) == []


def test_build_spec_refuses_before_touching_the_frame():
    p = seeded_project(YEAR)
    p.open_access.banking_enabled = True
    with pytest.raises(UnsupportedInput, match="banking"):
        build_spec(p, pd.DataFrame(), YEAR)


def test_build_spec_checks_fixed_capacities_only_in_manual_mode():
    with pytest.raises(UnsupportedInput, match="unknown capacity keys"):
        build_spec(seeded_project(YEAR), _day_frame(), YEAR, mode=Mode.MANUAL,
                   fixed_capacities={"solar_oa_mw": 10.0})


def test_the_roof_holds_what_it_holds_less_the_panels_already_on_it():
    """8 MWp of roof with a 1 MWp legacy array leaves 7, whatever the option allows."""
    p = seeded_project(YEAR)
    assert capacity_limits(p)["solar_onsite_mw"][1] == pytest.approx(7.0)
    roof = next(g for g in build_spec(p, _day_frame(), YEAR).generation
                if g.cap_key == "solar_onsite_mw")
    assert roof.max_mw == pytest.approx(7.0)
    assert any("outside the option's range" in s
               for s in _problems(p, fixed_capacities={"solar_onsite_mw": 8.0}))


def test_ground_mount_adds_to_the_roof():
    p = seeded_project(YEAR)
    p.project.site.land_mw_cap = 3.0
    p.asset_options[0].max_mw = 20.0
    assert capacity_limits(p)["solar_onsite_mw"][1] == pytest.approx(10.0)


def test_a_site_with_no_room_for_an_enabled_rooftop_option_is_refused():
    p = seeded_project(YEAR)
    p.project.site.roof_area_mw_cap = 1.0                 # exactly the legacy array
    assert any("leaves no room" in s for s in _problems(p))
    p.project.site.roof_area_mw_cap = 0.5                 # less than is already there
    assert any("exceeds the site's roof + land" in s for s in _problems(p))


def test_existing_plant_still_generates_when_its_option_is_disabled():
    p = seeded_project(YEAR)
    p.asset_options[0].enabled = False                     # no more rooftop, 1 MW exists
    spec = build_spec(p, _day_frame(), YEAR)
    roof = next(g for g in spec.generation if g.cap_key == "solar_onsite_mw")
    assert roof.existing_mw == pytest.approx(1.0)
    assert roof.max_mw == 0.0 and roof.min_mw == 0.0
