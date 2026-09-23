"""Discrete equipment sizes, checked against answers worked out on paper.

One MW of flat load and a panel profile at full output for eight hours. Each MW of solar
up to one MW saves 8 h x 8,000 INR/MWh = 64,000 INR; beyond one MW it is curtailed and
saves nothing. So the continuous optimum is exactly 1.0 MW whenever the annuity is under
64,000. In 0.4 MW units it must be 0.8 or 1.2 MW, and 1.2 wins only while the extra
0.4 MW costs less than the 0.2 MW of grid energy it displaces: 0.4a < 12,800, a < 32,000.
"""
from __future__ import annotations

import numpy as np
import pytest

from energy_core.optimization import solve_year, unsupported_input_problems
from energy_core.schemas.common import Mode, ValidationStatus
from energy_core.validation import certify
from project import seeded_project
from tiny import solar, tiny_spec

N = 96


def _spec(annuity, step):
    prof = np.zeros(N); prof[32:64] = 1.0
    return tiny_spec(n=N, load_mw=1.0, tariff_inr_per_mwh=8000.0,
                     gens=[solar(prof, cap_max=2.0, annuity=annuity, step=step)])


def _cert(spec, art, caps=None):
    return certify(spec, caps or art.capacities, art.dispatch, art.ledger,
                   solver_objective=art.outcome.objective, status=art.outcome.status,
                   gap=art.outcome.gap, horizon_blocks=N)


@pytest.mark.parametrize("annuity,step,want", [
    (20_000.0, None, 1.0),
    (20_000.0, 0.4, 1.2),
    (40_000.0, 0.4, 0.8),
])
def test_capacity_lands_on_the_step_the_arithmetic_picks(annuity, step, want):
    a = solve_year(_spec(annuity, step), time_limit_s=30, mip_gap=0.0)
    assert a.capacities.solar_onsite_mw == pytest.approx(want, abs=1e-6)
    grid_mwh = 24.0 - min(want, 1.0) * 8.0
    assert a.outcome.objective == pytest.approx(want * annuity + grid_mwh * 8000.0)


def test_a_stepped_optimum_is_a_mip_and_still_certifies():
    s = _spec(20_000.0, 0.4)
    a = solve_year(s, time_limit_s=30, mip_gap=0.0)
    assert a.outcome.n_integers == 1
    rep = _cert(s, a)
    assert rep.status is ValidationStatus.CERTIFIED, rep.material_issues
    assert "discrete_sizes" in rep.checks_run


def test_a_pinned_stepped_capacity_stays_an_lp():
    prof = np.zeros(N); prof[32:64] = 1.0
    s = tiny_spec(n=N, load_mw=1.0, tariff_inr_per_mwh=8000.0,
                  gens=[solar(prof, cap_max=2.0, annuity=20_000.0, step=0.4)],
                  mode=Mode.MANUAL, fixed={"solar_onsite_mw": 0.8})
    a = solve_year(s, time_limit_s=30)
    assert a.outcome.n_integers == 0 and a.outcome.gap == 0.0
    assert a.capacities.solar_onsite_mw == pytest.approx(0.8)


def test_the_validator_rejects_an_off_grid_capacity():
    s = _spec(20_000.0, 0.4)
    a = solve_year(s, time_limit_s=30, mip_gap=0.0)
    rep = _cert(s, a, a.capacities.model_copy(update={"solar_onsite_mw": 1.0}))
    assert rep.status is ValidationStatus.FAILED
    assert any(i.check == "step_solar_onsite_mw" for i in rep.material_issues)


def test_the_validator_rejects_a_capacity_above_the_option_limit():
    s = _spec(20_000.0, None)
    a = solve_year(s, time_limit_s=30)
    rep = _cert(s, a, a.capacities.model_copy(update={"solar_onsite_mw": 5.0}))
    assert any(i.check == "bounds_solar_onsite_mw" for i in rep.material_issues)


def test_min_and_step_together_restrict_the_choice():
    """min 0.5 in 0.4 MW units leaves 0.8, 1.2, 1.6, 2.0; at a high annuity the least
    of those is forced, where without the minimum nothing would be built at all."""
    prof = np.zeros(N); prof[32:64] = 1.0
    s = tiny_spec(n=N, load_mw=1.0, tariff_inr_per_mwh=8000.0,
                  gens=[solar(prof, cap_max=2.0, annuity=1e6, step=0.4, cap_min=0.5)])
    a = solve_year(s, time_limit_s=30, mip_gap=0.0)
    assert a.capacities.solar_onsite_mw == pytest.approx(0.8, abs=1e-6)


# ---------------------------------------------------------------- input checks
def _stepped_project(step=2.5):
    p = seeded_project(2026)
    p.asset_options[2].step_mw = step                  # wind in 2.5 MW turbines
    return p


def test_a_stepped_project_is_accepted():
    assert unsupported_input_problems(_stepped_project(), 2026) == []


def test_a_step_with_no_size_between_min_and_max_is_refused():
    p = seeded_project(2026)
    o = p.asset_options[0]
    o.min_mw, o.max_mw, o.step_mw = 1.0, 1.5, 2.0
    assert any("no multiple of step_mw" in s for s in unsupported_input_problems(p, 2026))


@pytest.mark.parametrize("caps,needle", [
    ({"wind_remote_mw": 6.0}, "not a whole multiple"),
    ({"wind_remote_mw": 7.5}, None),
    ({"solar_onsite_mw": 9.0}, "outside the option's range"),
    ({"bess_energy_mwh": 500.0}, "outside the option's range"),
])
def test_a_manual_scenario_must_be_one_mode_a_could_have_chosen(caps, needle):
    problems = unsupported_input_problems(_stepped_project(), 2026, fixed_capacities=caps)
    if needle is None:
        assert problems == []
    else:
        assert any(needle in s for s in problems), problems


def test_an_omitted_key_is_checked_at_zero():
    p = seeded_project(2026)
    p.asset_options[1].min_mw = 5.0
    problems = unsupported_input_problems(p, 2026, fixed_capacities={"wind_remote_mw": 0.0})
    assert any("solar_remote_mw = 0.0" in s for s in problems), problems
