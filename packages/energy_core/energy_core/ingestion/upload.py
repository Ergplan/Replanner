"""Turn a user's load file into the canonical 15-minute series, or say exactly why not.

Meter exports come in every shape: 15-minute or hourly, kW or kWh per interval, ISO or
day-first dates, a fiscal year that straddles two calendar years. What is never done here
is guess a missing value. A gap is reported with its timestamps and the file is refused,
because a filled gap is an invented demand peak or an invented saving.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from .chronology import build_index

Unit = Literal["kW", "MW", "kWh"]
_MINUTES = (15, 30, 60)


@dataclass
class LoadUpload:
    load_mw: np.ndarray | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    resolution_min: int | None = None
    rows_read: int = 0
    rows_used: int = 0
    time_column: str = ""
    value_column: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors and self.load_mw is not None

    def summary(self) -> dict:
        s = {"ok": self.ok, "errors": self.errors, "warnings": self.warnings,
             "resolution_min": self.resolution_min, "rows_read": self.rows_read,
             "rows_used": self.rows_used, "time_column": self.time_column,
             "value_column": self.value_column}
        if self.ok:
            s |= {"blocks": int(self.load_mw.size),
                  "annual_mwh": float(self.load_mw.sum() * 0.25),
                  "peak_mw": float(self.load_mw.max()),
                  "mean_mw": float(self.load_mw.mean())}
        return s


def _pick_columns(df: pd.DataFrame) -> tuple[str, str] | None:
    cols = list(df.columns)
    named_t = [c for c in cols if any(w in str(c).lower() for w in ("time", "date"))]
    t = named_t[0] if named_t else cols[0]
    rest = [c for c in cols if c != t]
    named_v = [c for c in rest if any(w in str(c).lower() for w in ("load", "kw", "mw", "demand"))]
    numeric = [c for c in rest if pd.to_numeric(df[c], errors="coerce").notna().mean() > 0.9]
    v = next((c for c in named_v if c in numeric), numeric[0] if numeric else None)
    return (t, v) if v is not None else None


def _parse_times(raw: pd.Series) -> tuple[pd.DatetimeIndex, str]:
    s = raw.astype(str).str.strip()
    iso = pd.to_datetime(s, errors="coerce", format="ISO8601")
    if iso.notna().mean() > 0.99:
        return pd.DatetimeIndex(iso), "ISO 8601"
    day_first = pd.to_datetime(s, errors="coerce", dayfirst=True, format="mixed")
    return pd.DatetimeIndex(day_first), "day-first"


def parse_load_csv(data: bytes, year: int, *, tz: str = "Asia/Kolkata",
                   unit: Unit = "kW") -> LoadUpload:
    """Read one timestamp column and one load column into MW on the canonical index.

    Timestamps are the START of each interval, in local clock time unless the file says
    otherwise. Rows outside the operating year are set aside so a longer export can be
    used as it is; inside the year, every interval must be present exactly once.
    """
    out = LoadUpload(load_mw=None)
    try:
        df = pd.read_csv(io.BytesIO(data))
    except Exception as exc:                                   # malformed, not a CSV
        out.errors.append(f"could not read the file as CSV: {exc}")
        return out
    out.rows_read = len(df)
    if df.shape[1] < 2 or not len(df):
        out.errors.append("expected at least two columns: a timestamp and a load value")
        return out
    picked = _pick_columns(df)
    if picked is None:
        out.errors.append("no numeric load column found")
        return out
    out.time_column, out.value_column = str(picked[0]), str(picked[1])

    ts, how = _parse_times(df[picked[0]])
    bad_t = np.flatnonzero(ts.isna())
    if bad_t.size:
        out.errors.append(f"{bad_t.size:,} timestamp(s) could not be read, first on row "
                          f"{int(bad_t[0]) + 2}: {df[picked[0]].iloc[int(bad_t[0])]!r}")
        return out
    ts = ts.tz_convert(tz) if ts.tz is not None else ts.tz_localize(
        tz, ambiguous="raise", nonexistent="raise")
    vals = pd.to_numeric(df[picked[1]], errors="coerce").to_numpy(dtype=float)

    start = pd.Timestamp(f"{year}-01-01", tz=tz)
    end = pd.Timestamp(f"{year + 1}-01-01", tz=tz)
    inside = (ts >= start) & (ts < end)
    if not inside.any():
        out.errors.append(f"no rows fall in {year}: the file runs from {ts.min()} to "
                          f"{ts.max()}. Set the project's operating year to match the data.")
        return out
    if (~inside).any():
        out.warnings.append(f"{int((~inside).sum()):,} row(s) outside {year} were ignored")
    ts, vals = ts[inside], vals[inside]

    dup = ts.duplicated()
    if dup.any():
        out.errors.append(f"{int(dup.sum()):,} duplicate timestamp(s), first {ts[dup][0]}")
        return out
    order = np.argsort(ts.asi8)
    ts, vals = ts[order], vals[order]

    step = int(pd.Series(ts).diff().dropna().dt.total_seconds().median() // 60) if len(ts) > 1 else 0
    if step not in _MINUTES:
        out.errors.append(f"interval of {step} minutes is not supported; use 15, 30 or 60")
        return out
    out.resolution_min = step

    want = pd.date_range(start, end, freq=f"{step}min", inclusive="left")
    missing = want.difference(ts)
    if len(missing):
        first = ", ".join(str(t) for t in missing[:3])
        out.errors.append(f"{len(missing):,} interval(s) missing in {year}, first: {first}. "
                          "Gaps are not filled automatically.")
    extra = ts.difference(want)
    if len(extra):
        out.errors.append(f"{len(extra):,} timestamp(s) are off the {step}-minute grid, "
                          f"first {extra[0]}")
    nonfinite = ~np.isfinite(vals)
    if nonfinite.any():
        out.errors.append(f"{int(nonfinite.sum()):,} load value(s) are blank or not numbers, "
                          f"first at {ts[np.flatnonzero(nonfinite)[0]]}")
    elif (vals < 0).any():
        k = int(np.flatnonzero(vals < 0)[0])
        out.errors.append(f"{int((vals < 0).sum()):,} negative load value(s), first "
                          f"{vals[k]} at {ts[k]}. Net export belongs in its own series.")
    if out.errors:
        return out

    hours = step / 60.0
    mw = {"kW": vals / 1000.0, "MW": vals, "kWh": vals / 1000.0 / hours}[unit]
    reps = step // 15
    load = np.repeat(mw, reps)
    if load.size != len(build_index(year, tz)):
        out.errors.append(f"{load.size:,} quarter-hours after expansion, expected "
                          f"{len(build_index(year, tz)):,}")
        return out
    if reps > 1:
        out.warnings.append(
            f"{step}-minute data is held flat across each quarter-hour, so the monthly "
            "demand charge only sees peaks at that resolution. 15-minute data is better.")
    if how == "day-first":
        out.warnings.append("dates were read day-first (DD-MM-YYYY)")
    out.rows_used = int(len(ts))
    out.load_mw = load
    return out
