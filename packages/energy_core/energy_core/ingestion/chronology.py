"""Chronology and unit checks.

A year of quarter-hours is a fixed, knowable thing: 35,040 blocks in a normal year and
35,136 in a leap year. Anything else is a defect in the data, not a rounding choice, and
truncating a leap day to reach 35,040 is the specific mistake this module exists to stop.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..schemas.common import BLOCKS_LEAP, BLOCKS_NON_LEAP, DT_HOURS


class ChronologyError(ValueError):
    """Raised when a series cannot be used for optimisation as it stands."""


DEFAULT_TZ = "Asia/Kolkata"


def build_index(year: int, tz: str = DEFAULT_TZ) -> pd.DatetimeIndex:
    """The canonical index for one operating year: UTC interval-start timestamps that
    span exactly one LOCAL calendar year.

    Billing months, time-of-day rules and the demand-charge peak are all local concepts,
    so a year that began at UTC midnight would spill into a thirteenth billing month and
    quietly mis-bill the first and last days. Anchoring on local midnight and storing UTC
    keeps the canonical key unambiguous and the billing calendar correct.
    """
    start = pd.Timestamp(f"{year}-01-01 00:00:00", tz=tz)
    end = pd.Timestamp(f"{year + 1}-01-01 00:00:00", tz=tz)
    local = pd.date_range(start=start, end=end, freq="15min", inclusive="left")
    return pd.DatetimeIndex(local.tz_convert("UTC"), name="timestamp_utc")


def expected_blocks(year: int, tz: str = DEFAULT_TZ) -> int:
    """Derived from the actual local span rather than assumed.

    In a zone without daylight saving this is 35,040, or 35,136 in a leap year. In a zone
    with it, a spring-forward year is two blocks shorter and an autumn-back year two
    longer, and asserting 35,040 there would reject correct data.
    """
    return len(build_index(year, tz))


@dataclass
class FrameReport:
    year: int
    n_rows: int
    n_expected: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_bad(self) -> "FrameReport":
        if self.errors:
            raise ChronologyError(
                f"{len(self.errors)} chronology/unit error(s) for {self.year}:\n  - "
                + "\n  - ".join(self.errors))
        return self


#: Columns whose physical range is known, as (low, high, inclusive-high).
RANGES: dict[str, tuple[float, float]] = {
    "load_mw": (0.0, np.inf),
    "solar_onsite_cf": (0.0, 1.0),
    "solar_remote_cf": (0.0, 1.0),
    "wind_remote_cf": (0.0, 1.0),
    "grid_available": (0.0, 1.0),
}


def validate_frame(df: pd.DataFrame, year: int, *, tz: str = DEFAULT_TZ,
                   required: tuple[str, ...] = ("load_mw",),
                   allow_duplicates: bool = False) -> FrameReport:
    """Check one aligned frame against the canonical index for `year`.

    Duplicates and gaps are errors by default. A correction is something a user has to
    ask for explicitly, so that it is logged and reproducible rather than silent.
    """
    rep = FrameReport(year=year, n_rows=len(df), n_expected=expected_blocks(year, tz))

    if "timestamp_utc" not in df.columns:
        rep.errors.append("no timestamp_utc column")
        return rep
    ts = pd.DatetimeIndex(df["timestamp_utc"])
    if ts.tz is None:
        rep.errors.append("timestamp_utc is timezone-naive; canonical time is UTC")
        return rep
    if str(ts.tz) != "UTC":
        rep.errors.append(f"timestamp_utc is {ts.tz}, not UTC")

    if len(df) != rep.n_expected:
        leap = calendar.isleap(year)
        rep.errors.append(
            f"{len(df):,} blocks, expected {rep.n_expected:,} for {year} "
            f"({'leap' if leap else 'non-leap'} year)")

    dup = int(ts.duplicated().sum())
    if dup and not allow_duplicates:
        first = ts[ts.duplicated()][0]
        rep.errors.append(f"{dup} duplicate timestamp(s), first at {first}")

    if not ts.is_monotonic_increasing:
        rep.errors.append("timestamps are not monotonically increasing")

    if len(ts) > 1:
        # pandas does not guarantee a nanosecond backing unit, so pin it before diffing
        secs = ts.to_numpy(dtype="datetime64[s]").astype("int64")
        deltas = np.diff(secs) / 3600.0                    # hours
        bad = np.flatnonzero(np.abs(deltas - DT_HOURS) > 1e-9)
        if bad.size:
            rep.errors.append(
                f"{bad.size} interval(s) are not {DT_HOURS} h; first gap at "
                f"{ts[bad[0]]} -> {ts[bad[0] + 1]} ({deltas[bad[0]]:.4f} h)")

    want = build_index(year, tz)
    if len(ts) == len(want) and not ts.equals(want):
        rep.errors.append("timestamps do not match the canonical index for the year")

    if "duration_hours" in df.columns:
        d = df["duration_hours"].to_numpy(dtype=float)
        if not np.allclose(d, DT_HOURS, atol=1e-12):
            rep.errors.append("duration_hours is not uniformly 0.25")

    for col in required:
        if col not in df.columns:
            rep.errors.append(f"required column {col!r} is missing")

    for col, (lo, hi) in RANGES.items():
        if col not in df.columns:
            continue
        v = df[col].to_numpy(dtype=float)
        if not np.isfinite(v).all():
            rep.errors.append(f"{col}: {int((~np.isfinite(v)).sum())} non-finite value(s)")
            continue
        off = np.flatnonzero((v < lo - 1e-9) | (v > hi + 1e-9))
        if off.size:
            rep.errors.append(
                f"{col}: {off.size} value(s) outside [{lo}, {hi}], first at index {off[0]}"
                f" = {v[off[0]]:.4f}")

    # Prices are allowed to be negative — that is real market behaviour, not bad data.
    for col in ("iex_buy_inr_per_kwh", "iex_sell_inr_per_kwh"):
        if col in df.columns:
            v = df[col].to_numpy(dtype=float)
            finite = np.isfinite(v) | pd.isna(df[col]).to_numpy()
            if not finite.all():
                rep.errors.append(f"{col}: non-finite values that are not nulls")
            if col in df.columns and (v[np.isfinite(v)] < 0).any():
                rep.warnings.append(f"{col}: negative prices present (valid, handled physically)")
    return rep
