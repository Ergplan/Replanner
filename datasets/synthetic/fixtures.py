"""Deliberately difficult datasets. Each one exists to make a specific failure mode
visible rather than to be solved successfully."""
from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "energy_core"))
sys.path.insert(0, str(Path(__file__).parent))
from generator import GenConfig, build  # noqa: E402


def duplicate_block(year: int = 2026) -> pd.DataFrame:
    """One timestamp appearing twice — must be rejected, not silently de-duplicated."""
    df, _ = build(GenConfig(year=year))
    return pd.concat([df, df.iloc[[5000]]]).sort_values("timestamp_utc").reset_index(drop=True)


def missing_blocks(year: int = 2026) -> pd.DataFrame:
    """A three-block hole. An inner join elsewhere would hide this."""
    df, _ = build(GenConfig(year=year))
    return df.drop(index=[20000, 20001, 20002]).reset_index(drop=True)


def leap_year() -> pd.DataFrame:
    """35,136 blocks. Truncating this to 35,040 is the mistake being guarded against."""
    df, _ = build(GenConfig(year=2028))
    return df


def leap_year_truncated() -> pd.DataFrame:
    df = leap_year()
    return df.iloc[:35_040].reset_index(drop=True)


def hourly_upload(year: int = 2026) -> pd.DataFrame:
    """An hourly load upload. Interpolating this to 15 minutes does not create measured
    data, and the quality flag has to survive the conversion."""
    df, _ = build(GenConfig(year=year))
    h = df.set_index("timestamp_utc").resample("1h").mean(numeric_only=True).reset_index()
    h["duration_hours"] = 1.0
    return h


ALL = {
    "duplicate_block": duplicate_block,
    "missing_blocks": missing_blocks,
    "leap_year": leap_year,
    "leap_year_truncated": leap_year_truncated,
    "hourly_upload": hourly_upload,
}
