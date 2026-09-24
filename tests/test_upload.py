"""A user's series file becomes the canonical series, or is refused with the reason."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energy_core.ingestion import build_index, parse_series_csv

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
    r = parse_series_csv(_csv(), YEAR, unit="kW")
    assert r.ok, r.errors
    assert r.values.size == N and r.values == pytest.approx(np.full(N, 5.0))
    assert r.summary()["annual_mwh"] == pytest.approx(5.0 * 8760)


def test_the_first_value_is_local_midnight_not_utc():
    v = np.zeros(N); v[0] = 9000.0                      # a spike at 00:00 IST, 1 January
    r = parse_series_csv(_csv(value=v), YEAR, unit="kW")
    assert r.values[0] == pytest.approx(9.0) and r.values[1:].max() == 0.0


@pytest.mark.parametrize("unit,value,mw", [("kW", 5000.0, 5.0), ("MW", 5.0, 5.0),
                                           ("kWh", 1250.0, 5.0)])   # 1,250 kWh in 15 min
def test_units(unit, value, mw):
    r = parse_series_csv(_csv(value=value), YEAR, unit=unit)
    assert r.values.mean() == pytest.approx(mw)


def test_hourly_is_held_flat_and_says_so():
    r = parse_series_csv(_csv(freq="60min", value=4000.0), YEAR, unit="kWh")
    assert r.ok and r.resolution_min == 60
    assert r.values == pytest.approx(np.full(N, 4.0))     # 4,000 kWh in an hour is 4 MW
    assert any("held flat" in w for w in r.warnings)


def test_day_first_dates_are_read_and_flagged():
    r = parse_series_csv(_csv(fmt="%d-%m-%Y %H:%M", cols=("Date Time", "Demand (kW)")), YEAR)
    assert r.ok, r.errors
    assert any("day-first" in w for w in r.warnings)
    assert (r.time_column, r.value_column) == ("Date Time", "Demand (kW)")


def test_a_longer_export_is_trimmed_to_the_year():
    r = parse_series_csv(_csv(start=f"{YEAR - 1}-04-01", end=f"{YEAR + 1}-03-31"), YEAR)
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
    r = parse_series_csv(_csv(**kw), YEAR)
    assert not r.ok
    assert any(needle in e for e in r.errors), r.errors


def test_a_blank_value_is_refused():
    t = pd.date_range(f"{YEAR}-01-01", f"{YEAR + 1}-01-01", freq="15min", inclusive="left")
    df = pd.DataFrame({"timestamp": t.strftime("%Y-%m-%d %H:%M"), "load_kw": 1.0})
    df.loc[500, "load_kw"] = None
    r = parse_series_csv(df.to_csv(index=False).encode(), YEAR)
    assert any("blank or not numbers" in e for e in r.errors)


def test_not_a_csv_at_all():
    assert not parse_series_csv(b"\x00\x01\x02", YEAR).ok


# ---------------------------------------------------------------- series other than load
@pytest.mark.parametrize("unit,value,cf", [("fraction", 0.25, 0.25), ("%", 25.0, 0.25)])
def test_output_profiles_in_either_unit(unit, value, cf):
    r = parse_series_csv(_csv(value=value, cols=("time", "pv_output")), YEAR,
                         column="solar_remote_cf", unit=unit)
    assert r.ok, r.errors
    assert r.summary()["annual_cf"] == pytest.approx(cf)


def test_output_above_capacity_is_refused():
    r = parse_series_csv(_csv(value=37.0, cols=("time", "wind")), YEAR,
                         column="wind_remote_cf", unit="fraction")
    assert not r.ok and any("cannot exceed 1" in e for e in r.errors)


def test_a_per_cent_file_read_as_fractions_is_questioned():
    r = parse_series_csv(_csv(value=0.004, cols=("time", "cf")), YEAR,
                         column="solar_onsite_cf", unit="fraction")
    assert r.ok and any("per cent" in w for w in r.warnings)


def test_exchange_prices_per_mwh_and_negative_prices_pass():
    v = np.full(N, 4500.0); v[100] = -1200.0
    r = parse_series_csv(_csv(value=v, cols=("time", "MCP (Rs/MWh)")), YEAR,
                         column="iex_buy_inr_per_kwh", unit="INR/MWh")
    assert r.ok, r.errors
    assert r.values[100] == pytest.approx(-1.2) and r.summary()["max"] == pytest.approx(4.5)


def test_availability_counts_outage_hours():
    v = np.ones(N); v[400:424] = 0.0                       # six hours down
    r = parse_series_csv(_csv(value=v, cols=("time", "grid_available")), YEAR,
                         column="grid_available")
    assert r.summary()["outage_hours"] == pytest.approx(6.0)


def test_an_unknown_unit_is_refused():
    r = parse_series_csv(_csv(), YEAR, column="load_mw", unit="amps")
    assert not r.ok and "not one of" in r.errors[0]
