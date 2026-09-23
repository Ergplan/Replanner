"""The HTTP boundary.

It accepts work and reads artefacts. It never solves: a request handler that blocks for
three minutes is not an API, and a result that exists only in this process's memory is
not a result.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "worker"))
sys.path.insert(0, str(ROOT / "packages" / "energy_core"))
sys.path.insert(0, str(ROOT / "datasets" / "synthetic"))

import jobs as J  # noqa: E402
from energy_core.optimization.supported import (  # noqa: E402
    CAP_KEY_BY_TECH, capacity_limits, unsupported_input_problems,
)

app = FastAPI(title="Least-cost energy digital twin", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3210"],
                   allow_methods=["*"], allow_headers=["*"])


class ScenarioRequest(BaseModel):
    project_id: str = "seed-industrial-mh"
    year: int = 2026
    mode: str = Field(default="manual", pattern="^(manual|find_optimum)$")
    capacities: dict[str, float] | None = None
    baseline_run_id: str | None = None


def _summary(run_id: str) -> dict:
    p = J.run_dir(run_id) / "summary.json"
    if not p.exists():
        raise HTTPException(404, f"no run {run_id}")
    return json.loads(p.read_text())


@app.get("/health")
def health():
    return {"ok": True, "runs": len(list(J.RUNS.glob("*/summary.json")))}


@app.get("/project")
def project(year: int = 2026):
    """Bounds and assumptions the sliders must respect. The UI reads its limits from
    here rather than hardcoding them, so the backend stays the authority."""
    from project import seeded_project
    inp = seeded_project(year)
    limits = capacity_limits(inp)
    caps = {}
    for o in inp.asset_options:
        # Effective limits, not the option's own: rooftop solar is also bounded by the
        # roof, less the panels already on it.
        lo, hi, step = limits.get(CAP_KEY_BY_TECH.get(o.technology), (0.0, 0.0, None))
        caps[o.option_id] = {"min_mw": lo, "max_mw": hi, "step_mw": step,
                             "enabled": o.enabled,
                             "technology": o.technology, "route": o.route.value}
    b = inp.battery
    return {
        "project_id": inp.project.project_id, "name": inp.project.name,
        "timezone": inp.project.timezone, "currency": inp.project.currency,
        "provenance": "synthetic", "illustrative_only": True,
        "site": inp.project.site.model_dump(),
        "options": caps,
        "battery": {"min_power_mw": b.min_power_mw, "max_power_mw": b.max_power_mw,
                    "min_energy_mwh": b.min_energy_mwh, "max_energy_mwh": b.max_energy_mwh,
                    "max_c_rate": b.technical.max_c_rate},
        "tariff": {"contract_demand_mw": inp.tariff.contract_demand_mw,
                   "demand_basis": inp.tariff.demand_basis.value,
                   "duty_frac": inp.tariff.duty_frac},
        "finance": inp.finance.model_dump(),
    }


@app.get("/runs")
def list_runs():
    out = []
    for p in sorted(J.RUNS.glob("*/summary.json"), key=lambda q: q.stat().st_mtime, reverse=True):
        s = json.loads(p.read_text())
        out.append({k: s.get(k) for k in (
            "run_id", "mode", "status", "validation_status", "coverage",
            "objective_inr_year", "gap", "wall_seconds", "input_fingerprint",
            "baseline_run_id", "model_version")} | {"run_id": s.get("run_id", p.parent.name)})
    return out


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    s = _summary(run_id)
    v = J.run_dir(run_id) / "validation.json"
    if v.exists():
        s["validation"] = json.loads(v.read_text())
    m = J.run_dir(run_id) / "monthly.csv"
    if m.exists():
        s["monthly"] = pd.read_csv(m).to_dict(orient="records")
    return s


@app.get("/runs/{run_id}/dispatch")
def dispatch(run_id: str, start: int = Query(0, ge=0), limit: int = Query(96, ge=1, le=2016)):
    """A window of solved blocks. The full year is never shipped to a browser; playback
    asks for the day it is showing."""
    p = J.run_dir(run_id) / "dispatch.parquet"
    if not p.exists():
        raise HTTPException(404, f"no dispatch for {run_id}")
    df = pd.read_parquet(p)
    n = len(df)
    win = df.iloc[start:start + limit].copy()
    win["timestamp_utc"] = win["timestamp_utc"].astype(str)
    return {"run_id": run_id, "n_blocks": n, "start": start, "count": len(win),
            "columns": list(win.columns),
            "rows": json.loads(win.to_json(orient="records"))}


@app.get("/runs/{run_id}/daily")
def daily(run_id: str):
    """Whole-year context for the charts, downsampled to days.

    Downsampling is a plotting convenience only. Optimisation and certification ran on
    every one of the 15-minute blocks; nothing here feeds back into either.
    """
    p = J.run_dir(run_id) / "dispatch.parquet"
    if not p.exists():
        raise HTTPException(404, f"no dispatch for {run_id}")
    df = pd.read_parquet(p)
    df["day"] = pd.DatetimeIndex(df["timestamp_utc"]).tz_convert("Asia/Kolkata").normalize()
    use_cols = [c for c in df.columns if c.startswith("use_")]
    # A deposit in the bank is wheeled energy that did not reach the site that day.
    if "bank_in_mw" in df.columns:
        df["_banked"] = -df["bank_in_mw"]
        use_cols.append("_banked")
    g = df.groupby("day")
    out = pd.DataFrame({
        "day": [str(d.date()) for d in g.size().index],
        "load_mwh": (g["load_mw"].sum() * 0.25).to_numpy(),
        "renewable_mwh": (g[use_cols].sum().sum(axis=1) * 0.25).to_numpy(),
        "import_utility_mwh": (g["import_utility_mw"].sum() * 0.25).to_numpy(),
        "import_market_mwh": (g["import_market_mw"].sum() * 0.25).to_numpy(),
        "discharge_mwh": (g["battery_discharge_mw"].sum() * 0.25).to_numpy(),
        "charge_mwh": (g["battery_charge_mw"].sum() * 0.25).to_numpy(),
        "curtailed_mwh": (g[[c for c in df.columns if c.startswith("curtail_")]]
                          .sum().sum(axis=1) * 0.25).to_numpy(),
        "peak_import_mw": g["import_utility_mw"].max().to_numpy(),
        "soc_min_mwh": g["soc_end_mwh"].min().to_numpy(),
        "soc_max_mwh": g["soc_end_mwh"].max().to_numpy(),
    })
    return {"run_id": run_id, "days": json.loads(out.to_json(orient="records"))}


@app.post("/scenarios")
def submit(req: ScenarioRequest):
    if req.mode == "manual" and not req.capacities:
        raise HTTPException(400, "a manual scenario must carry capacities")
    # The worker runs the same check; running it here turns a job that would fail
    # minutes later into an immediate, readable refusal.
    from project import seeded_project
    problems = unsupported_input_problems(
        seeded_project(req.year), req.year,
        fixed_capacities=req.capacities if req.mode == "manual" else None)
    if problems:
        raise HTTPException(400, {"unsupported_inputs": problems})
    job = J.new_job(req.project_id, req.mode, req.year, req.capacities, req.baseline_run_id)
    return {"job_id": job.job_id, "status": job.status}


@app.get("/jobs")
def list_jobs(limit: int = 25):
    return [j.__dict__ for j in J.all_jobs()[:limit]]


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    j = J.load(job_id)
    if j is None:
        raise HTTPException(404, f"no job {job_id}")
    return j.__dict__


@app.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    j = J.load(job_id)
    if j is None:
        raise HTTPException(404, f"no job {job_id}")
    if j.status in J.TERMINAL:
        return j.__dict__
    j.status = J.CANCELLED if j.status == J.QUEUED else J.CANCELLING
    return j.save().__dict__
