"""The lifetime of a design, checked against arithmetic done on paper."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energy_core.ingestion import build_index
from energy_core.lifetime import (
    LifetimeResult, YearResult, capital_schedule, evaluate, finish, interpolate, irr,
    sample_years, year_spec,
)
from energy_core.optimization import build_spec
from project import seeded_project

CR = 1e7
YEAR = 2026


def _frame(n=96, load=5.0):
    return pd.DataFrame({"timestamp_utc": build_index(YEAR)[:n], "load_mw": load,
                         "solar_onsite_cf": np.r_[np.zeros(32), np.full(32, 0.7), np.zeros(n - 64)],
                         "solar_remote_cf": 0.25, "wind_remote_cf": 0.3,
                         "iex_buy_inr_per_kwh": 5.0, "grid_available": 1.0})


# ---------------------------------------------------------------- capital
def test_cells_are_replaced_once_and_leave_two_thirds_of_their_life_unused():
    p = seeded_project(YEAR)            # cells last 15 years, the PCS 20, a 20-year study
    capex0, repl, salvage = capital_schedule(p, {"bess_energy_mwh": 10.0, "bess_power_mw": 5.0}, 20)
    assert capex0 == pytest.approx(10 * 1.8 * CR + 5 * 1.2 * CR)
    assert repl == [{"year": 15, "item": "bess_energy", "inr": pytest.approx(10 * 1.8 * CR)}]
    assert salvage == pytest.approx(10 * 1.8 * CR * 10 / 15)     # bought at 15, used 5 of 15


def test_replacements_escalate_to_the_year_they_are_bought():
    p = seeded_project(YEAR)
    _, repl, _ = capital_schedule(p, {"bess_energy_mwh": 10.0}, 20, escalation=0.05)
    assert repl[0]["inr"] == pytest.approx(10 * 1.8 * CR * 1.05 ** 15)


def test_sampled_years_bracket_each_replacement():
    assert sample_years(seeded_project(YEAR), 20) == [1, 5, 10, 15, 16, 20]


# ---------------------------------------------------------------- one year of the life
def test_year_k_degrades_fades_and_escalates():
    p = seeded_project(YEAR)
    p.finance.opex_escalation = 0.04
    base = build_spec(p, _frame(), YEAR)
    design = {"solar_remote_mw": 10.0, "bess_energy_mwh": 20.0, "bess_power_mw": 5.0}
    y3 = year_spec(base, p, design, 3, 0.04)
    oa = next(g for g in y3.generation if g.key == "solar_oa")
    assert oa.profile == pytest.approx(next(g for g in base.generation if g.key == "solar_oa").profile
                                       * 0.995 ** 2)
    assert y3.fixed_capacities["bess_energy_mwh"] == pytest.approx(20.0 * 0.98 ** 2)
    assert y3.energy_inr_per_mwh == pytest.approx(base.energy_inr_per_mwh * 1.04 ** 2)
    assert y3.oa_charge_inr_per_mwh == pytest.approx(base.oa_charge_inr_per_mwh * 1.04 ** 2)
    y16 = year_spec(base, p, design, 16, 0.04)
    assert y16.fixed_capacities["bess_energy_mwh"] == pytest.approx(20.0)   # new cells
    assert base.fixed_capacities is None                                    # base untouched


# ---------------------------------------------------------------- the money
def test_irr_of_textbook_flows():
    assert irr(np.array([-100.0, 110.0])) == pytest.approx(0.10, abs=1e-9)
    assert irr(np.array([-100.0, 60.0, 60.0])) == pytest.approx(0.130662, abs=1e-5)
    assert irr(np.array([100.0, 10.0])) is None


def test_interpolation_is_straight_between_solved_years():
    assert interpolate({1: 10.0, 3: 30.0}, 3) == pytest.approx([10.0, 20.0, 30.0])


def test_npv_levelised_cost_payback_and_return_on_paper():
    """Buy for 100 and pay 10 a year, or pay the grid 70 a year; two years at 10%."""
    res = LifetimeResult(study_years=2, discount_rate=0.10, escalation=0.0, basis="real",
                         design={}, sampled_years=[1, 2],
                         years=[YearResult(1, True, 10.0, 70.0), YearResult(2, True, 10.0, 70.0)],
                         capex_year0=100.0, replacements=[], salvage=0.0, annual_kwh=1000.0)
    finish(res, seeded_project(YEAR))
    pv_kwh = 1000 / 1.1 + 1000 / 1.21
    assert res.npv_design == pytest.approx(100 + 10 / 1.1 + 10 / 1.21)
    assert res.npv_grid_only == pytest.approx(70 / 1.1 + 70 / 1.21)
    assert res.levelised_design == pytest.approx(res.npv_design / pv_kwh)
    assert res.payback_year == 2                          # -100, then -40, then +20
    assert res.irr == pytest.approx(0.130662, abs=1e-5)


# ---------------------------------------------------------------- end to end, one day long
def test_a_design_is_run_through_its_years():
    p = seeded_project(YEAR)
    p.finance.study_period_years = 3
    p.finance.opex_escalation = 0.05
    design = {"solar_onsite_mw": 2.0, "solar_remote_mw": 4.0, "wind_remote_mw": 3.0,
              "bess_power_mw": 1.0, "bess_energy_mwh": 4.0}
    res = evaluate(p, _frame(), design, year=YEAR, horizon_blocks=96, time_limit_s=60)
    assert res.problems == []
    assert [y.sampled for y in res.years] == [True, False, True]
    assert all(y.validation == "certified" for y in res.years if y.sampled)
    y1, y2, y3 = res.years
    assert y2.opex_design == pytest.approx((y1.opex_design + y3.opex_design) / 2)
    # Prices rise 5% a year and panels lose output, so the grid bill grows at least 5%.
    assert y3.opex_grid_only / y1.opex_grid_only == pytest.approx(1.05 ** 2, rel=0.02)
    assert res.npv_design is not None and res.levelised_design > 0


def test_a_design_that_cannot_serve_the_site_is_reported_not_priced():
    p = seeded_project(YEAR)
    p.finance.study_period_years = 2
    p.project.site.import_limit_mw = 1.0                   # 5 MW of load, 1 MW of wires
    res = evaluate(p, _frame(), {"bess_power_mw": 0.5, "bess_energy_mwh": 1.0}, year=YEAR,
                   horizon_blocks=96, time_limit_s=60)
    assert res.npv_design is None
    assert any("cannot serve the site" in s for s in res.problems)
    assert any("grid supply alone cannot serve" in s for s in res.problems)
