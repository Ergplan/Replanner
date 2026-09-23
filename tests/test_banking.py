"""Banking of wheeled energy, against answers worked out on paper.

One day, 1 MW of flat load at 8,000 INR/MWh. A 2 MW wheeled solar plant already exists
and delivers at full output from 08:00 to 16:00, so the site has a 1 MW surplus for
eight hours — 8 MWh — and needs 16 MWh from the grid overnight. Without banking the
surplus is curtailed and the day costs 16 x 8,000 = 128,000 INR.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energy_core.ingestion import build_index
from energy_core.optimization import build_spec, solve_year
from energy_core.schemas.common import ValidationStatus
from energy_core.validation import certify
from project import seeded_project
from tiny import bank, solar, tiny_spec

N, DT, TARIFF = 96, 0.25, 8000.0
NO_BANK = 16 * TARIFF


def _spec(banking=None):
    prof = np.zeros(N); prof[32:64] = 1.0
    oa = solar(prof, cap_max=0.0, existing=2.0, key="oa", cap_key="solar_remote_mw",
               wheeled=True)
    return tiny_spec(n=N, load_mw=1.0, tariff_inr_per_mwh=TARIFF, gens=[oa], banking=banking)


def _solve(banking=None):
    s = _spec(banking)
    return s, solve_year(s, time_limit_s=30)


def _cert(s, a, dispatch=None, ledger=None):
    return certify(s, a.capacities, a.dispatch if dispatch is None else dispatch,
                   a.ledger if ledger is None else ledger,
                   solver_objective=a.outcome.objective, status=a.outcome.status,
                   gap=a.outcome.gap, horizon_blocks=N)


@pytest.mark.parametrize("banking,cost", [
    (None, NO_BANK),
    (bank(), NO_BANK - 8 * TARIFF),                               # all 8 MWh come back
    (bank(charge_frac=0.1), NO_BANK - 7.2 * TARIFF),              # the licensee keeps 10%
    (bank(charge=500.0), NO_BANK - 8 * TARIFF + 8 * 500.0),       # paid per MWh deposited
    (bank(charge=9_000.0), NO_BANK),                              # dearer than the grid: unused
    (bank(blocked_hours=range(16, 22)), NO_BANK - 2 * TARIFF),    # only 22:00-24:00 drawable
    (bank(cap_mwh=[3.0]), NO_BANK - 3 * TARIFF),                  # at most 3 MWh deposited
])
def test_banking_saves_exactly_what_the_rules_allow(banking, cost):
    s, a = _solve(banking)
    assert a.outcome.objective == pytest.approx(cost)
    assert _cert(s, a).status is ValidationStatus.CERTIFIED


def test_unused_energy_lapses_at_settlement():
    """Settle at noon. The morning's 4 MWh of surplus has no deficit left in its period
    to serve and lapses; the afternoon's 4 MWh serves the evening."""
    period = [0] * 48 + [1] * 48
    _, a = _solve(bank(period=period))
    assert a.outcome.objective == pytest.approx(NO_BANK - 4 * TARIFF)
    _, credited = _solve(bank(period=period, credit=1_000.0))
    assert credited.outcome.objective == pytest.approx(NO_BANK - 4 * TARIFF - 4 * 1_000.0)
    assert credited.dispatch["bank_lapse_mwh"].sum() == pytest.approx(4.0)
    assert credited.ledger.banking_lapse_credit == pytest.approx(-4_000.0)


def test_a_period_cannot_open_on_energy_it_did_not_bank():
    _, a = _solve(bank())
    d = a.dispatch
    assert d["bank_out_mw"].iloc[:32].abs().max() == pytest.approx(0.0)
    assert d["bank_balance_mwh"].iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_renewable_used_excludes_what_was_banked():
    _, a = _solve(bank())
    d = a.dispatch
    assert d["renewable_used_mw"].iloc[32:64].to_numpy() == pytest.approx(np.ones(32))
    assert d["use_oa_mw"].iloc[32:64].to_numpy() == pytest.approx(np.full(32, 2.0))


# ---------------------------------------------------------------- the validator
@pytest.mark.parametrize("column,block,delta,check", [
    ("bank_balance_mwh", 70, 0.5, "bank_balance"),
    ("bank_out_mw", 70, 3.0, "bank_overdrawn"),
    ("bank_lapse_mwh", 95, 1.0, "bank_lapse"),
    ("bank_in_mw", 40, 5.0, "bank_source"),
])
def test_validator_rebuilds_the_bank_itself(column, block, delta, check):
    s, a = _solve(bank())
    d = a.dispatch.copy()
    d.loc[block, column] += delta
    rep = _cert(s, a, dispatch=d)
    assert rep.status is ValidationStatus.FAILED
    assert any(i.check == check for i in rep.material_issues), \
        [i.check for i in rep.material_issues]


def test_validator_catches_drawal_in_a_blocked_hour():
    s, a = _solve(bank(blocked_hours=range(16, 22)))
    d = a.dispatch.copy()
    d.loc[70, "bank_out_mw"] = 0.5                     # 17:30, blocked
    assert any(i.check == "bank_drawal_hours" for i in _cert(s, a, dispatch=d).material_issues)


def test_validator_catches_a_bank_the_project_does_not_have():
    s, a = _solve(None)
    d = a.dispatch.copy()
    d.loc[40, "bank_in_mw"] = 0.5
    assert any(i.check == "bank_disabled" for i in _cert(s, a, dispatch=d).material_issues)


def test_validator_catches_a_banking_charge_never_charged():
    s, a = _solve(bank(charge=500.0))
    tampered = a.ledger.model_copy(update={"banking_charges": 0.0})
    assert any(i.check == "ledger_banking_charges"
               for i in _cert(s, a, ledger=tampered).material_issues)


# ---------------------------------------------------------------- from project inputs
def test_blocked_hours_are_local_clock_hours():
    """The canonical index is UTC; the rules are written in IST."""
    p = seeded_project(2026)
    oa = p.open_access
    oa.banking_enabled = True
    oa.banking_drawal_blocked_hours = [18, 19, 20, 21]
    oa.banking_cap_frac_of_load = 0.3
    frame = pd.DataFrame({"timestamp_utc": build_index(2026)[:96], "load_mw": 5.0,
                          "solar_onsite_cf": 0.5, "solar_remote_cf": 0.5,
                          "wind_remote_cf": 0.5})
    bk = build_spec(p, frame, 2026).banking
    blocked = np.flatnonzero(~bk.drawal_allowed)
    assert blocked.tolist() == list(range(72, 88))        # 18:00-22:00 local on day one
    assert bk.cap_mwh.tolist() == pytest.approx([0.3 * 5.0 * 24])


def test_banking_off_leaves_the_fingerprint_alone():
    p = seeded_project(2026)
    frame = pd.DataFrame({"timestamp_utc": build_index(2026)[:96], "load_mw": 5.0,
                          "solar_onsite_cf": 0.5, "solar_remote_cf": 0.5,
                          "wind_remote_cf": 0.5})
    before = build_spec(p, frame, 2026).fingerprint()
    p.open_access.banking_enabled = True
    assert build_spec(p, frame, 2026).fingerprint() != before
