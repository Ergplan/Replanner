"""Analytical, physical and adversarial checks.

Each test here either has an answer that can be worked out on paper, or corrupts a known
good result and insists the validator notices.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energy_core.finance import crf, eac_from_npv, npv, salvage_fraction
from energy_core.ingestion import ChronologyError, build_index, expected_blocks, validate_frame
from energy_core.optimization import build_spec, solve_year
from energy_core.schemas.common import Mode, SolveStatus, ValidationStatus
from energy_core.validation import Tolerances, certify
from tiny import battery, solar, tiny_spec

DT = 0.25


# ---------------------------------------------------------------- chronology
def test_block_counts_and_leap_years():
    assert expected_blocks(2026) == 35_040
    assert expected_blocks(2028) == 35_136
    assert len(build_index(2026)) == 35_040


def test_local_year_gives_twelve_billing_months():
    idx = build_index(2026)
    local = idx.tz_convert("Asia/Kolkata")
    assert local[0].strftime("%Y-%m-%d %H:%M") == "2026-01-01 00:00"
    assert len(set(zip(local.year, local.month))) == 12


@pytest.mark.parametrize("mutate,expect", [
    (lambda d: d.drop(index=[10, 11]), "blocks"),
    (lambda d: pd.concat([d, d.iloc[[7]]]).sort_values("timestamp_utc"), "duplicate"),
])
def test_bad_chronology_is_rejected(mutate, expect):
    df = pd.DataFrame({"timestamp_utc": build_index(2026), "load_mw": 1.0,
                       "duration_hours": DT})
    rep = validate_frame(mutate(df), 2026)
    assert not rep.ok and any(expect in e for e in rep.errors)


def test_leap_year_must_not_be_truncated():
    df = pd.DataFrame({"timestamp_utc": build_index(2028), "load_mw": 1.0,
                       "duration_hours": DT})
    with pytest.raises(ChronologyError):
        validate_frame(df.iloc[:35_040], 2028).raise_if_bad()


# ---------------------------------------------------------------- finance
def test_crf_matches_textbook_and_degrades_to_straight_line():
    assert crf(0.10, 20) == pytest.approx(0.1174596, rel=1e-6)
    assert crf(0.0, 20) == pytest.approx(0.05)


def test_npv_to_equivalent_annual_cost_round_trips():
    assert eac_from_npv(npv([100.0] * 20, 0.10, t0=1), 0.10, 20) == pytest.approx(100.0)


def test_salvage_leaves_a_stub_when_life_does_not_divide_the_horizon():
    assert salvage_fraction(15, 20) == pytest.approx(10 / 15)
    assert salvage_fraction(10, 20) == pytest.approx(0.0)


# ---------------------------------------------------------------- analytical dispatch
def test_grid_only_cost_is_energy_times_price():
    a = solve_year(tiny_spec(n=96, load_mw=1.0, tariff_inr_per_mwh=8000.0), time_limit_s=30)
    assert a.outcome.objective == pytest.approx(1.0 * 24 * 8000.0)


def test_free_solar_displaces_grid_exactly():
    prof = np.zeros(96); prof[32:64] = 1.0            # eight hours at full output
    s = tiny_spec(n=96, load_mw=1.0, tariff_inr_per_mwh=8000.0,
                  gens=[solar(prof, cap_max=1.0, annuity=0.0)])
    a = solve_year(s, time_limit_s=30)
    assert a.capacities.solar_onsite_mw == pytest.approx(1.0)
    assert a.outcome.objective == pytest.approx((24 - 8) * 8000.0)


def test_solar_is_not_built_when_its_annuity_exceeds_the_energy_it_saves():
    prof = np.zeros(96); prof[32:64] = 1.0
    saved = 8 * 8000.0
    s = tiny_spec(n=96, load_mw=1.0, tariff_inr_per_mwh=8000.0,
                  gens=[solar(prof, cap_max=1.0, annuity=saved * 1.05)])
    assert solve_year(s, time_limit_s=30).capacities.solar_onsite_mw == pytest.approx(0.0)


def test_battery_arbitrages_a_price_step():
    """Cheap for six hours, dear for eighteen, with a lossless free 1 MW / 4 MWh battery.

    Only the cost is determinate here. With no efficiency loss and no wear, cycling is
    free, so the optimal face contains many dispatches and asserting a particular one
    would be asserting which vertex HiGHS happened to stop on.
    """
    tar = np.full(96, 10_000.0); tar[:24] = 1_000.0
    s = tiny_spec(n=96, load_mw=1.0, tariff_inr_per_mwh=tar,
                  battery=battery(power=1.0, energy=4.0))
    a = solve_year(s, time_limit_s=30)
    without = 6 * 1.0 * 1_000.0 + 18 * 1.0 * 10_000.0
    shifted = 4.0 * (10_000.0 - 1_000.0)          # one full cycle out of the dear hours
    assert a.outcome.objective == pytest.approx(without - shifted)
    soc = a.dispatch["soc_end_mwh"].to_numpy()
    assert soc.max() - soc.min() == pytest.approx(4.0, abs=1e-6)


def test_round_trip_efficiency_is_applied_once_to_energy():
    tar = np.full(96, 10_000.0); tar[:24] = 1_000.0
    s = tiny_spec(n=96, load_mw=1.0, tariff_inr_per_mwh=tar,
                  battery=battery(power=1.0, energy=4.0, eta_c=0.9, eta_d=0.9))
    d = solve_year(s, time_limit_s=30).dispatch
    ch = d["battery_charge_mw"].sum() * DT
    dis = d["battery_discharge_mw"].sum() * DT
    assert dis / ch == pytest.approx(0.81, abs=1e-6)     # 0.9 x 0.9, and only once


def test_a_cyclic_year_cannot_start_with_free_energy():
    s = tiny_spec(n=96, load_mw=1.0, tariff_inr_per_mwh=8000.0,
                  battery=battery(power=2.0, energy=8.0, eta_c=0.9, eta_d=0.9))
    d = solve_year(s, time_limit_s=30).dispatch
    soc = d["soc_end_mwh"].to_numpy()
    assert soc[-1] == pytest.approx(d["soc_start_mwh"].to_numpy()[0], abs=1e-9)
    assert d["battery_discharge_mw"].sum() <= d["battery_charge_mw"].sum() + 1e-9


def test_zero_battery_is_well_behaved():
    a = solve_year(tiny_spec(n=96, load_mw=1.0), time_limit_s=30)
    assert a.capacities.bess_power_mw == 0.0
    assert a.capacities.bess_duration_h is None          # no division by zero
    assert a.dispatch["battery_discharge_mw"].abs().max() == pytest.approx(0.0)


def test_existing_capacity_supplies_energy_but_is_never_repurchased():
    prof = np.zeros(96); prof[32:64] = 1.0
    s = tiny_spec(n=96, load_mw=1.0, tariff_inr_per_mwh=8000.0,
                  gens=[solar(prof, cap_max=0.0, annuity=1e9, existing=1.0)])
    a = solve_year(s, time_limit_s=30)
    assert a.ledger.annualised_capex_new == pytest.approx(0.0)
    assert a.outcome.objective == pytest.approx((24 - 8) * 8000.0)


def test_negative_prices_do_not_buy_and_sell_in_the_same_block():
    tar = np.full(96, 6_000.0)
    iex = np.full(96, 4_000.0); iex[40:48] = -2_000.0     # paid to take energy
    s = tiny_spec(n=96, load_mw=1.0, tariff_inr_per_mwh=tar, iex_buy=iex, market=True,
                  export_limit=5.0)
    s.export_inr_per_mwh = 3_000.0
    a = solve_year(s, time_limit_s=60)
    d = a.dispatch
    imp = d["import_utility_mw"] + d["import_market_mw"]
    exp = d["export_utility_mw"] + d["export_market_mw"]
    assert not ((imp > 1e-6) & (exp > 1e-6)).any()
    assert np.isfinite(a.outcome.objective)


def test_demand_charge_sees_a_quarter_hour_spike_that_hourly_data_hides():
    load = np.full(96, 1.0); load[40] = 9.0              # one block, four times the hour
    hourly_peak = load.reshape(24, 4).mean(axis=1).max()
    s = tiny_spec(n=96, load_mw=load, tariff_inr_per_mwh=0.0, demand_rate=100_000.0,
                  import_limit=20.0)
    a = solve_year(s, time_limit_s=30)
    billed = a.monthly["billing_demand_mw"].iloc[0]
    assert billed == pytest.approx(9.0)
    assert billed > hourly_peak                          # 9.0 against 3.0
    assert a.outcome.objective == pytest.approx(9.0 * 100_000.0)


# ---------------------------------------------------------------- mode B
def _mode_a_then_b(n=96):
    tar = np.full(n, 10_000.0); tar[:24] = 1_000.0
    prof = np.zeros(n); prof[32:64] = 1.0
    mk = lambda mode, fixed: tiny_spec(
        n=n, load_mw=1.0, tariff_inr_per_mwh=tar,
        gens=[solar(prof, cap_max=2.0, annuity=5_000.0)],
        battery=battery(power=2.0, energy=8.0, annuity_mw=1_000.0, annuity_mwh=500.0),
        mode=mode, fixed=fixed)
    a = solve_year(mk(Mode.FIND_OPTIMUM, None), time_limit_s=60)
    fixed = {"solar_onsite_mw": a.capacities.solar_onsite_mw,
             "bess_power_mw": a.capacities.bess_power_mw,
             "bess_energy_mwh": a.capacities.bess_energy_mwh}
    b = solve_year(mk(Mode.MANUAL, fixed), time_limit_s=60)
    return a, b, fixed


def test_mode_b_at_the_optimum_reproduces_the_optimum():
    a, b, _ = _mode_a_then_b()
    assert b.outcome.objective == pytest.approx(a.outcome.objective, rel=1e-9)


def test_mode_b_capacities_stay_exactly_where_they_were_put():
    _, b, fixed = _mode_a_then_b()
    for k, v in fixed.items():
        assert getattr(b.capacities, k) == pytest.approx(v, abs=1e-9)


def test_a_manual_scenario_cannot_beat_a_certified_optimum():
    a, _, _ = _mode_a_then_b()
    tar = np.full(96, 10_000.0); tar[:24] = 1_000.0
    prof = np.zeros(96); prof[32:64] = 1.0
    worse = solve_year(tiny_spec(
        n=96, load_mw=1.0, tariff_inr_per_mwh=tar,
        gens=[solar(prof, cap_max=2.0, annuity=5_000.0)],
        battery=battery(power=2.0, energy=8.0, annuity_mw=1_000.0, annuity_mwh=500.0),
        mode=Mode.MANUAL,
        fixed={"solar_onsite_mw": 0.0, "bess_power_mw": 0.0, "bess_energy_mwh": 0.0}),
        time_limit_s=60)
    assert worse.outcome.objective >= a.outcome.objective - 1e-6


# ---------------------------------------------------------------- the validator itself
def _certified_tiny():
    tar = np.full(96, 10_000.0); tar[:24] = 1_000.0
    s = tiny_spec(n=96, load_mw=1.0, tariff_inr_per_mwh=tar, demand_rate=1_000.0,
                  battery=battery(power=1.0, energy=4.0, eta_c=0.95, eta_d=0.95))
    a = solve_year(s, time_limit_s=30)
    rep = certify(s, a.capacities, a.dispatch, a.ledger, solver_objective=a.outcome.objective,
                  status=a.outcome.status, gap=a.outcome.gap, horizon_blocks=96)
    return s, a, rep


def test_a_good_run_certifies():
    _, _, rep = _certified_tiny()
    assert rep.status is ValidationStatus.CERTIFIED
    assert rep.blocks_checked == rep.blocks_expected == 96
    assert rep.material_issues == []


@pytest.mark.parametrize("column,delta,check", [
    ("soc_end_mwh", 0.5, "soc_recursion"),
    ("import_utility_mw", 0.5, "energy_balance"),
    ("battery_discharge_mw", 0.5, "soc_recursion"),
    ("use_pv_mw", 0.0, None),
])
def test_validator_detects_a_corrupted_block(column, delta, check):
    s, a, _ = _certified_tiny()
    if column not in a.dispatch.columns:
        pytest.skip(f"{column} not present in this fixture")
    d = a.dispatch.copy()
    victim = 47
    d.loc[victim, column] = d.loc[victim, column] + delta
    rep = certify(s, a.capacities, d, a.ledger, solver_objective=a.outcome.objective,
                  status=a.outcome.status, gap=a.outcome.gap, horizon_blocks=96)
    assert rep.status is ValidationStatus.FAILED
    hit = [i for i in rep.material_issues if i.check == check]
    assert hit, f"expected {check}, got {[i.check for i in rep.material_issues]}"
    assert any(i.block_index == victim for i in rep.material_issues)


def test_validator_detects_a_cost_that_was_never_charged():
    s, a, _ = _certified_tiny()
    tampered = a.ledger.model_copy(update={"utility_energy": a.ledger.utility_energy * 0.5})
    rep = certify(s, a.capacities, a.dispatch, tampered,
                  solver_objective=a.outcome.objective, status=a.outcome.status,
                  gap=a.outcome.gap, horizon_blocks=96)
    assert rep.status is ValidationStatus.FAILED
    assert any(i.check == "ledger_utility_energy" for i in rep.material_issues)


def test_validator_refuses_to_claim_coverage_it_did_not_check():
    s, a, _ = _certified_tiny()
    rep = certify(s, a.capacities, a.dispatch.iloc[:50], a.ledger,
                  solver_objective=a.outcome.objective, status=a.outcome.status,
                  gap=a.outcome.gap, horizon_blocks=96)
    assert rep.status is ValidationStatus.FAILED
    assert rep.coverage_statement == "50 / 96 blocks checked"
