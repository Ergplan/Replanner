"""Deterministic synthetic industrial dataset.

Everything here is illustrative. It is not a vendor quotation, not a metered load and not
a statement of any statutory charge. It exists so the engine can be exercised end to end
and so the hard cases have somewhere to live.

Shapes are physically grounded where that is cheap to do — the solar profile comes from a
declination and hour-angle model rather than a hand-drawn bell — so that seasonal and
diurnal structure is real even though the weather is invented.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "energy_core"))
from energy_core.ingestion import build_index, validate_frame  # noqa: E402
from energy_core.schemas.common import DT_HOURS  # noqa: E402

LAT_DEG = 19.0          # a notional western-India industrial site
TZ = "Asia/Kolkata"


@dataclass
class GenConfig:
    year: int = 2026
    seed: int = 20260101
    base_load_mw: float = 6.0
    day_shift_mw: float = 6.4
    weekend_factor: float = 0.62
    shutdown_start_doy: int = 288        # a week of annual maintenance in mid-October
    shutdown_days: int = 7
    spike_doy: int = 122                 # the one-block demand spike fixture
    spike_mw: float = 17.5
    outage_doy: int = 196                # a six-hour grid outage in July
    outage_hours: float = 6.0
    roof_peak_cf: float = 0.79
    remote_solar_gain: float = 1.5663      # a better site, tracker-assisted: ~25% annual CF
    wind_scale: float = 0.6324             # calibrated to ~30% annual CF
    iex_base_inr_per_kwh: float = 4.4


def _solar_clearsky(idx_local: pd.DatetimeIndex, lat_deg: float) -> np.ndarray:
    """Clear-sky capacity factor from solar geometry: declination, hour angle, and a
    cosine-of-incidence on a fixed tilt approximated by horizontal irradiance."""
    doy = idx_local.dayofyear.to_numpy()
    hour = idx_local.hour.to_numpy() + idx_local.minute.to_numpy() / 60.0 + DT_HOURS / 2
    decl = np.deg2rad(23.45) * np.sin(2 * np.pi * (284 + doy) / 365.0)
    lat = np.deg2rad(lat_deg)
    omega = np.deg2rad(15.0 * (hour - 12.0))
    cos_z = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(omega)
    return np.clip(cos_z, 0.0, None)


def _monsoon(idx_local: pd.DatetimeIndex) -> np.ndarray:
    """A smooth seasonal attenuation peaking through the June-September monsoon."""
    doy = idx_local.dayofyear.to_numpy()
    return 1.0 - 0.42 * np.exp(-0.5 * ((doy - 210) / 42.0) ** 2)


def build(cfg: GenConfig) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(cfg.seed)
    idx = build_index(cfg.year)
    local = idx.tz_convert(TZ)
    n = len(idx)
    doy = local.dayofyear.to_numpy()
    hour = local.hour.to_numpy() + local.minute.to_numpy() / 60.0
    dow = local.dayofweek.to_numpy()          # Monday = 0

    # ---- load: two shifts, a quieter Sunday, an annual shutdown ----
    shift = np.where((hour >= 6.0) & (hour < 22.0), 1.0, 0.0)
    ramp = 0.5 * (1 + np.tanh((hour - 5.5) * 2.2)) * 0.5 * (1 + np.tanh((21.8 - hour) * 2.2))
    load = cfg.base_load_mw + cfg.day_shift_mw * np.maximum(shift * 0.55, ramp)
    load *= np.where(dow == 6, cfg.weekend_factor, np.where(dow == 5, 0.88, 1.0))
    load *= 1.0 + 0.035 * np.sin(2 * np.pi * (doy - 40) / 365.0)      # mild seasonality
    shutdown = (doy >= cfg.shutdown_start_doy) & (doy < cfg.shutdown_start_doy + cfg.shutdown_days)
    load = np.where(shutdown, 1.9 + 0.3 * rng.random(n), load)
    load += rng.normal(0.0, 0.16, n)
    load = np.clip(load, 0.6, None)

    # A single quarter-hour spike. An hourly model averages this away and under-sizes the
    # demand charge; the 15-minute model does not.
    spike_block = int(np.flatnonzero((doy == cfg.spike_doy) & (np.abs(hour - 14.25) < 1e-9))[0])
    load[spike_block] = cfg.spike_mw

    # ---- renewables ----
    clear = _solar_clearsky(local, LAT_DEG)
    season = _monsoon(local)
    daily_cloud = rng.beta(7.0, 1.6, size=366)[doy - 1]
    solar_onsite = np.clip(cfg.roof_peak_cf * clear * season * daily_cloud
                           * (1 + rng.normal(0, 0.05, n)), 0.0, 1.0)
    solar_remote = np.clip(solar_onsite * cfg.remote_solar_gain
                           * (1 + rng.normal(0, 0.03, n)), 0.0, 1.0)

    wind_season = 0.30 + 0.46 * np.exp(-0.5 * ((doy - 205) / 55.0) ** 2)
    wind_diurnal = 1.0 + 0.22 * np.sin(2 * np.pi * (hour - 15.0) / 24.0)
    gust = np.convolve(rng.normal(0, 1, n + 96), np.ones(96) / 96, mode="same")[:n]
    wind = np.clip(cfg.wind_scale * wind_season * wind_diurnal * (1 + 0.55 * gust), 0.0, 1.0)

    # ---- grid availability: one planned outage ----
    grid = np.ones(n)
    out_mask = (doy == cfg.outage_doy) & (hour >= 10.0) & (hour < 10.0 + cfg.outage_hours)
    grid[out_mask] = 0.0

    # ---- market prices: a morning and an evening peak, cheap solar hours ----
    shape = (1.0 + 0.55 * np.exp(-0.5 * ((hour - 9.0) / 1.6) ** 2)
             + 0.95 * np.exp(-0.5 * ((hour - 20.0) / 2.1) ** 2)
             - 0.38 * np.exp(-0.5 * ((hour - 13.0) / 2.4) ** 2))
    seasonal = 1.0 + 0.20 * np.exp(-0.5 * ((doy - 135) / 45.0) ** 2)   # pre-monsoon scarcity
    vol = np.exp(rng.normal(0.0, 0.26, n))
    buy = cfg.iex_base_inr_per_kwh * shape * seasonal * vol
    # Negative prices: a handful of high-solar, low-demand shoulder-season middays. These
    # are valid data and the model must stay physical through them.
    neg = (np.abs(hour - 12.5) < 1.0) & np.isin(doy, [61, 62, 68, 305, 306])
    buy = np.where(neg, -0.9 - 1.4 * rng.random(n), buy)
    buy = np.clip(buy, -3.0, 22.0)
    sell = buy * 0.97

    df = pd.DataFrame({
        "timestamp_utc": idx,
        "duration_hours": DT_HOURS,
        "load_mw": np.round(load, 6),
        "solar_onsite_cf": np.round(solar_onsite, 6),
        "solar_remote_cf": np.round(solar_remote, 6),
        "wind_remote_cf": np.round(wind, 6),
        "grid_available": grid,
        "iex_buy_inr_per_kwh": np.round(buy, 4),
        "iex_sell_inr_per_kwh": np.round(sell, 4),
    })

    manifest = {
        "provenance": "synthetic",
        "illustrative_only": True,
        "note": ("Generated example data. Not metered load, not a vendor quotation and not "
                 "a statement of any statutory charge."),
        "config": asdict(cfg),
        "year": cfg.year,
        "n_blocks": int(n),
        "dt_hours": DT_HOURS,
        "annual_energy_mwh": float(df.load_mw.sum() * DT_HOURS),
        "peak_load_mw": float(df.load_mw.max()),
        "peak_block_utc": str(df.timestamp_utc[int(df.load_mw.idxmax())]),
        "load_factor": float(df.load_mw.mean() / df.load_mw.max()),
        "solar_onsite_cf_annual": float(df.solar_onsite_cf.mean()),
        "solar_remote_cf_annual": float(df.solar_remote_cf.mean()),
        "wind_remote_cf_annual": float(df.wind_remote_cf.mean()),
        "negative_price_blocks": int((df.iex_buy_inr_per_kwh < 0).sum()),
        "outage_blocks": int((df.grid_available < 1).sum()),
        "shutdown_blocks": int(shutdown.sum()),
        "spike_block_index": spike_block,
        "spike_block_utc": str(df.timestamp_utc[spike_block]),
    }
    return df, manifest


def write(out_dir: Path, cfg: GenConfig) -> dict:
    df, manifest = build(cfg)
    validate_frame(df, cfg.year).raise_if_bad()
    out_dir.mkdir(parents=True, exist_ok=True)
    pq = out_dir / f"industrial_{cfg.year}.parquet"
    df.to_parquet(pq, index=False)
    manifest["parquet"] = pq.name
    (out_dir / f"industrial_{cfg.year}.manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate the seeded industrial dataset.")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--seed", type=int, default=20260101)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent)
    a = ap.parse_args()
    m = write(a.out, GenConfig(year=a.year, seed=a.seed))
    print(json.dumps({k: v for k, v in m.items() if k != "config"}, indent=2))
