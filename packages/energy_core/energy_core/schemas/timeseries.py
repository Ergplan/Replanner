"""Time-series metadata and the aligned frame every model run consumes.

Canonical time is a UTC interval-start timestamp. Intervals are half-open [start, end)
and always 0.25 h. Local billing time is derived from the project's IANA timezone, never
stored as the primary key, so a DST-free zone like Asia/Kolkata and a DST zone behave
the same way.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

import numpy as np
from pydantic import BaseModel, Field

from .common import DT_HOURS, Provenance, QualityFlag

#: Columns every aligned frame carries. Optional columns stay nullable — a missing
#: price is not a zero price.
CORE_COLUMNS = (
    "timestamp_utc", "duration_hours", "load_mw", "solar_onsite_cf",
    "solar_remote_cf", "wind_remote_cf", "grid_available", "tariff_rule_id",
    "iex_buy_inr_per_kwh", "iex_sell_inr_per_kwh",
)


class TimeSeriesMeta(BaseModel):
    dataset_id: str
    version: str
    project_id: str
    variable: str
    units: str
    timezone: str = "Asia/Kolkata"
    interval_minutes: int = 15
    start_utc: datetime
    end_utc: datetime
    calendar_year: int
    weather_year: int | None = None
    source: str = ""
    provenance: Provenance = Provenance.SYNTHETIC
    quality_flags: list[QualityFlag] = Field(default_factory=lambda: [QualityFlag.OK])
    content_hash: str = ""
    n_blocks: int = 0

    def stamp(self, values: np.ndarray) -> "TimeSeriesMeta":
        """Attach a content hash over the exact bytes used downstream."""
        h = hashlib.sha256(np.ascontiguousarray(values, dtype=np.float64).tobytes()).hexdigest()
        return self.model_copy(update={"content_hash": h[:32], "n_blocks": int(values.shape[0])})


class TimeSeriesBundle(BaseModel):
    """The metadata side of an aligned frame. The numbers themselves live in a
    pandas frame / Parquet file, never in a JSON cell."""

    project_id: str
    operating_year: int
    n_blocks: int
    dt_hours: float = DT_HOURS
    series: dict[str, TimeSeriesMeta] = Field(default_factory=dict)
    frame_hash: str = ""

    @property
    def annual_hours(self) -> float:
        return self.n_blocks * self.dt_hours
