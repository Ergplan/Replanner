"""Turn a user's time series into the canonical 15-minute series, or say exactly why not.

Meter exports, generation logs and exchange price files come in every shape: 15-minute
or hourly, kW or kWh per interval, INR per kWh or per MWh, ISO or day-first dates, a
fiscal year that straddles two calendar years. What is never done here is guess a
missing value. A gap is reported with its timestamps and the file is refused, because a
filled gap is an invented demand peak, an invented sunny hour or an invented price.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .chronology import build_index

_MINUTES = (15, 30, 60)

# Each converter takes the raw values and the interval length in hours.
Convert = Callable[[np.ndarray, float], np.ndarray]


@dataclass(frozen=True)
class SeriesSpec:
    column: str
    label: str
    units: dict[str, Convert]          # the first is the default
    lo: float
    hi: float
    keywords: tuple[str, ...]          # how its value column is usually headed
    out_of_range: str = ""             # what a value outside [lo, hi] usually means


_CF = {"fraction": lambda v, h: v, "%": lambda v, h: v / 100.0}

SPECS: dict[str, SeriesSpec] = {s.column: s for s in (
    SeriesSpec("load_mw", "Site load",
               {"kW": lambda v, h: v / 1000.0, "MW": lambda v, h: v,
                "kWh": lambda v, h: v / 1000.0 / h},
               0.0, np.inf, ("load", "kw", "mw", "demand", "consumption"),
               "Net export belongs in its own series."),
    SeriesSpec("solar_onsite_cf", "Rooftop solar output per MWp", _CF, 0.0, 1.0,
               ("cf", "output", "generation", "solar", "pv"),
               "Output per unit of capacity cannot exceed 1 (100%)."),
    SeriesSpec("solar_remote_cf", "Open-access solar output per MW", _CF, 0.0, 1.0,
               ("cf", "output", "generation", "solar", "pv"),
               "Output per unit of capacity cannot exceed 1 (100%)."),
    SeriesSpec("wind_remote_cf", "Open-access wind output per MW", _CF, 0.0, 1.0,
               ("cf", "output", "generation", "wind"),
               "Output per unit of capacity cannot exceed 1 (100%)."),
    SeriesSpec("iex_buy_inr_per_kwh", "Exchange purchase price",
               {"INR/kWh": lambda v, h: v, "INR/MWh": lambda v, h: v / 1000.0},
               -np.inf, np.inf, ("price", "mcp", "rate", "inr", "rs")),
    SeriesSpec("grid_available", "Grid availability", _CF, 0.0, 1.0,
               ("avail", "grid", "status", "supply"),
               "Availability is 1 when the grid is up and 0 during an outage."),
)}


@dataclass
class SeriesUpload:
    column: str
    values: np.ndarray | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    resolution_min: int | None = None
    rows_read: int = 0
    rows_used: int = 0
    time_column: str = ""
    value_column: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors and self.values is not None

    def summary(self) -> dict:
        s = {"ok": self.ok, "column": self.column, "errors": self.errors,
             "warnings": self.warnings, "resolution_min": self.resolution_min,
             "rows_read": self.rows_read, "rows_used": self.rows_used,
             "time_column": self.time_column, "value_column": self.value_column}
        if self.ok:
            v = self.values
            s["blocks"] = int(v.size)
            s |= describe(self.column, v)
        return s


def describe(column: str, v: np.ndarray) -> dict:
    """What a series amounts to, in the terms a user would check it by."""
    if column == "load_mw":
        return {"annual_mwh": float(v.sum() * 0.25), "peak_mw": float(v.max()),
                "mean_mw": float(v.mean())}
    if column.endswith("_cf"):
        return {"annual_cf": float(v.mean())}
    if column == "grid_available":
        return {"outage_hours": float((v < 1).sum() * 0.25)}
    return {"mean": float(np.nanmean(v)), "min": float(np.nanmin(v)), "max": float(np.nanmax(v))}


def _pick_columns(df: pd.DataFrame, keywords: tuple[str, ...]) -> tuple[str, str] | None:
    cols = list(df.columns)
    named_t = [c for c in cols if any(w in str(c).lower() for w in ("time", "date"))]
    t = named_t[0] if named_t else cols[0]
    rest = [c for c in cols if c != t]
    named_v = [c for c in rest if any(w in str(c).lower() for w in keywords)]
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


def parse_series_csv(data: bytes, year: int, *, column: str = "load_mw",
                     unit: str | None = None, tz: str = "Asia/Kolkata") -> SeriesUpload:
    """Read one timestamp column and one value column onto the canonical index.

    Timestamps are the START of each interval, in local clock time unless the file says
    otherwise. Rows outside the operating year are set aside so a longer export can be
    used as it is; inside the year, every interval must be present exactly once.
    """
    spec = SPECS[column]
    unit = unit or next(iter(spec.units))
    out = SeriesUpload(column=column, values=None)
    if unit not in spec.units:
        out.errors.append(f"unit {unit!r} is not one of {list(spec.units)} for {spec.label.lower()}")
        return out
    try:
        df = pd.read_csv(io.BytesIO(data))
    except Exception as exc:                                   # malformed, not a CSV
        out.errors.append(f"could not read the file as CSV: {exc}")
        return out
    out.rows_read = len(df)
    if df.shape[1] < 2 or not len(df):
        out.errors.append("expected at least two columns: a timestamp and a value")
        return out
    picked = _pick_columns(df, spec.keywords)
    if picked is None:
        out.errors.append("no numeric value column found")
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
        out.errors.append(f"{int(nonfinite.sum()):,} value(s) are blank or not numbers, "
                          f"first at {ts[np.flatnonzero(nonfinite)[0]]}")
    if out.errors:
        return out

    hours = step / 60.0
    canon = spec.units[unit](vals, hours)
    off = np.flatnonzero((canon < spec.lo - 1e-9) | (canon > spec.hi + 1e-9))
    if off.size:
        k = int(off[0])
        what = ("negative value(s)" if spec.lo == 0 and canon[k] < 0
                else f"value(s) outside {spec.lo:g} to {spec.hi:g} after converting from {unit}")
        out.errors.append(f"{off.size:,} {what}, first {vals[k]} at {ts[k]}. "
                          f"{spec.out_of_range}".strip())
        return out

    reps = step // 15
    series = np.repeat(canon, reps)
    if series.size != len(build_index(year, tz)):
        out.errors.append(f"{series.size:,} quarter-hours after expansion, expected "
                          f"{len(build_index(year, tz)):,}")
        return out
    if reps > 1:
        note = (", so the monthly demand charge only sees peaks at that resolution"
                if column == "load_mw" else "")
        out.warnings.append(f"{step}-minute data is held flat across each quarter-hour{note}. "
                            "15-minute data is better.")
    if how == "day-first":
        out.warnings.append("dates were read day-first (DD-MM-YYYY)")
    if column.endswith("_cf") and unit == "fraction" and canon.max() <= 0.01 and canon.max() > 0:
        out.warnings.append("every value is 1% or less of capacity: was the file in per cent?")
    out.rows_used = int(len(ts))
    out.values = series
    return out
