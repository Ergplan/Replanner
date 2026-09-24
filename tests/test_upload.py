"""A user's load file becomes the canonical series, or is refused with the reason."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energy_core.ingestion import build_index, parse_load_csv

YEAR = 2026
N = len(build_index(YEAR))


def _csv(freq="15min", value=5000.0, fmt="%Y-%m-%d %H:%M", start=f"{YEAR}-01-01",
         end=f"{YEAR + 1}-01-01", cols=("timestamp", "load_kw"), drop=(), dup=()):
    t = pd.date_range(start, end, freq=freq, inclusive="left")
    v = np.full(len(t), value) if np.isscalar(value) else np.asarray(value)
    df = pd.DataFrame({cols[0]: t.strftime(fmt), cols[1]: v})
    df = df.drop(index=list(drop))
    if dup:
        df = pd.concat([df, df.iloc[list(dup)]])
    return df.to_csv(index=False).encode()


def test_fifteen_minute_kw_lands_on_the_canonical_index():
    r = parse_load_csv(_csv(), YEAR, unit="kW")
    assert r.ok, r.errors
    assert r.load_mw.size == N and r.load_mw == pytest.approx(np.full(N, 5.0))
    assert r.summary()["annual_mwh"] == pytest.approx(5.0 * 8760)


def test_the_first_value_is_local_midnight_not_utc():
    v = np.zeros(N); v[0] = 9000.0                      # a spike at 00:00 IST, 1 January
    r = parse_load_csv(_csv(value=v), YEAR, unit="kW")
    assert r.load_mw[0] == pytest.approx(9.0) and r.load_mw[1:].max() == 0.0


@pytest.mark.parametrize("unit,value,mw", [("kW", 5000.0, 5.0), ("MW", 5.0, 5.0),
                                           ("kWh", 1250.0, 5.0)])   # 1,250 kWh in 15 min
def test_units(unit, value, mw):
    r = parse_load_csv(_csv(value=value), YEAR, unit=unit)
    assert r.load_mw.mean() == pytest.approx(mw)


def test_hourly_is_held_flat_and_says_so():
    r = parse_load_csv(_csv(freq="60min", value=4000.0), YEAR, unit="kWh")
    assert r.ok and r.resolution_min == 60
    assert r.load_mw == pytest.approx(np.full(N, 4.0))     # 4,000 kWh in an hour is 4 MW
    assert any("held flat" in w for w in r.warnings)


def test_day_first_dates_are_read_and_flagged():
    r = parse_load_csv(_csv(fmt="%d-%m-%Y %H:%M", cols=("Date Time", "Demand (kW)")), YEAR)
    assert r.ok, r.errors
    assert any("day-first" in w for w in r.warnings)
    assert (r.time_column, r.value_column) == ("Date Time", "Demand (kW)")


def test_a_longer_export_is_trimmed_to_the_year():
    r = parse_load_csv(_csv(start=f"{YEAR - 1}-04-01", end=f"{YEAR + 1}-03-31"), YEAR)
    assert r.ok, r.errors
    assert any("outside" in w for w in r.warnings)


@pytest.mark.parametrize("kw,needle", [
    (dict(drop=(100, 101)), "2 interval(s) missing"),
    (dict(dup=(7,)), "duplicate"),
    (dict(start=f"{YEAR}-03-01"), "missing"),                 # starts in March
    (dict(start=f"{YEAR + 3}-01-01", end=f"{YEAR + 4}-01-01"), "no rows fall in"),
    (dict(freq="5min", end=f"{YEAR}-01-02"), "not supported"),
    (dict(value=-1.0), "negative"),
])
def test_gaps_and_nonsense_are_refused_not_filled(kw, needle):
    r = parse_load_csv(_csv(**kw), YEAR)
    assert not r.ok
    assert any(needle in e for e in r.errors), r.errors


def test_a_blank_value_is_refused():
    t = pd.date_range(f"{YEAR}-01-01", f"{YEAR + 1}-01-01", freq="15min", inclusive="left")
    df = pd.DataFrame({"timestamp": t.strftime("%Y-%m-%d %H:%M"), "load_kw": 1.0})
    df.loc[500, "load_kw"] = None
    r = parse_load_csv(df.to_csv(index=False).encode(), YEAR)
    assert any("blank or not numbers" in e for e in r.errors)


def test_not_a_csv_at_all():
    assert not parse_load_csv(b"\x00\x01\x02", YEAR).ok
