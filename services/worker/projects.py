"""Projects the user owns: their inputs, and any series they uploaded.

The seeded example is where every new project starts, not the only project there is. A
project is a folder under runs/_projects/<id>/ with inputs.json (the full ProjectInputs)
and a series/ folder holding whatever the user uploaded. Anything not uploaded comes from
the synthetic sample for the operating year, and the page says so, series by series.

Grid availability is the exception. The sample carries a deliberate six-hour outage to
exercise the solver. Applied to a real site, that would invent an outage it never had,
so a user project assumes the grid is always there unless told otherwise.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "energy_core"))
sys.path.insert(0, str(ROOT / "datasets" / "synthetic"))

from energy_core.ingestion import build_index  # noqa: E402
from energy_core.schemas.domain import ProjectInputs  # noqa: E402

PROJECTS = ROOT / "runs" / "_projects"
SAMPLE_DIR = ROOT / "datasets" / "synthetic"
SAMPLE_ID = "seed-industrial-mh"

#: Series a project may supply, with what each one is.
SERIES = {
    "load_mw": "Site load",
    "solar_onsite_cf": "Rooftop solar output per MWp",
    "solar_remote_cf": "Open-access solar output per MW",
    "wind_remote_cf": "Open-access wind output per MW",
    "iex_buy_inr_per_kwh": "Exchange purchase price",
    "grid_available": "Grid availability",
}


class ProjectNotFound(KeyError):
    pass


def _dir(pid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", pid):
        raise ProjectNotFound(pid)
    return PROJECTS / pid


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def operating_year(inputs: ProjectInputs) -> int:
    return inputs.project.operating_years[0]


def load_inputs(pid: str) -> ProjectInputs:
    p = _dir(pid) / "inputs.json"
    if p.exists():
        return ProjectInputs.model_validate_json(p.read_text())
    if pid == SAMPLE_ID:
        from project import seeded_project
        return seeded_project(2026)
    raise ProjectNotFound(pid)


class SampleIsReadOnly(PermissionError):
    pass


def save_inputs(pid: str, inputs: ProjectInputs) -> None:
    if pid == SAMPLE_ID:
        raise SampleIsReadOnly("the sample is read-only; create a project from it to edit")
    if not (_dir(pid) / "inputs.json").exists():
        raise ProjectNotFound(pid)
    inputs.project.project_id = pid
    _write_atomic(_dir(pid) / "inputs.json", inputs.model_dump_json(indent=2))


def create_project(name: str) -> str:
    """A new project starts as a copy of the sample, under the user's name."""
    from project import seeded_project
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "project"
    pid = f"{slug}-{uuid.uuid4().hex[:6]}"
    inputs = seeded_project(2026)
    inputs.project.project_id, inputs.project.name = pid, name
    _write_atomic(_dir(pid) / "inputs.json", inputs.model_dump_json(indent=2))
    return pid


def list_projects() -> list[dict]:
    out = [{"project_id": SAMPLE_ID, "name": load_inputs(SAMPLE_ID).project.name,
            "sample": True}]
    if PROJECTS.exists():
        for p in sorted(PROJECTS.glob("*/inputs.json"), key=lambda q: q.stat().st_mtime,
                        reverse=True):
            pid = p.parent.name
            if pid == SAMPLE_ID:
                continue
            try:
                name = json.loads(p.read_text())["project"]["name"]
            except Exception:
                continue
            out.append({"project_id": pid, "name": name, "sample": False})
    return out


def sample_frame(year: int) -> pd.DataFrame:
    """The synthetic year, generated on first use for a year that has none yet."""
    pq = SAMPLE_DIR / f"industrial_{year}.parquet"
    if not pq.exists():
        from generator import GenConfig, write
        write(SAMPLE_DIR, GenConfig(year=year))
    return pd.read_parquet(pq)


def save_series(pid: str, year: int, column: str, values: np.ndarray) -> None:
    if pid == SAMPLE_ID:
        raise SampleIsReadOnly("the sample is read-only; create a project from it to upload")
    if column not in SERIES:
        raise KeyError(column)
    d = _dir(pid) / "series"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{column}.{year}.parquet.tmp"
    pd.DataFrame({column: np.asarray(values, dtype=float)}).to_parquet(tmp, index=False)
    os.replace(tmp, d / f"{column}.{year}.parquet")


def delete_series(pid: str, year: int, column: str) -> None:
    (_dir(pid) / "series" / f"{column}.{year}.parquet").unlink(missing_ok=True)


def _uploaded(pid: str, year: int) -> dict[str, Path]:
    if pid == SAMPLE_ID:
        return {}
    d = _dir(pid) / "series"
    return {c: d / f"{c}.{year}.parquet" for c in SERIES
            if (d / f"{c}.{year}.parquet").exists()}


def load_frame(pid: str, year: int, tz: str = "Asia/Kolkata") -> pd.DataFrame:
    """The aligned frame a solve reads: uploads where there are any, sample elsewhere."""
    base = sample_frame(year)
    if pid == SAMPLE_ID:
        return base
    frame = base.copy()
    frame["timestamp_utc"] = build_index(year, tz)
    frame["grid_available"] = 1.0
    for col, path in _uploaded(pid, year).items():
        frame[col] = pd.read_parquet(path)[col].to_numpy(dtype=float)
    return frame


def series_status(pid: str, year: int) -> list[dict]:
    """Where each series comes from, and what it amounts to, for the setup page."""
    up = _uploaded(pid, year)
    frame = load_frame(pid, year)
    out = []
    for col, label in SERIES.items():
        v = frame[col].to_numpy(dtype=float) if col in frame.columns else None
        if col in up:
            source = "uploaded"
        elif col == "grid_available" and pid != SAMPLE_ID:
            source = "assumed: always available"
        else:
            source = "sample"
        row = {"column": col, "label": label, "source": source}
        if v is not None and v.size:
            if col == "load_mw":
                row |= {"annual_mwh": float(v.sum() * 0.25), "peak_mw": float(v.max())}
            elif col.endswith("_cf"):
                row |= {"annual_cf": float(v.mean())}
            elif col == "grid_available":
                row |= {"outage_hours": float((v < 1).sum() * 0.25)}
            else:
                row |= {"mean": float(np.nanmean(v))}
        out.append(row)
    return out


def input_version(pid: str, year: int) -> float:
    """Changes whenever the inputs or an upload change; keys the fingerprint cache."""
    paths = [_dir(pid) / "inputs.json", *_uploaded(pid, year).values()]
    return max((p.stat().st_mtime for p in paths if p.exists()), default=0.0)
