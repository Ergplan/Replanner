"""A file-backed job store.

Job and run state lives on disk, not in the memory of whichever process happened to
accept the request. The API writes a queued job and returns; the worker picks it up,
solves, certifies, persists the artefacts, and only then marks the job succeeded. A
restart of either process loses nothing, and a result is never announced before it
can be read back.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOBS = ROOT / "runs" / "_jobs"
RUNS = ROOT / "runs"

QUEUED, RUNNING, CANCELLING, CANCELLED = "queued", "running", "cancelling", "cancelled"
SUCCEEDED, FAILED = "succeeded", "failed"
TERMINAL = {SUCCEEDED, FAILED, CANCELLED}


@dataclass
class Job:
    job_id: str
    project_id: str
    mode: str
    year: int
    capacities: dict | None = None
    status: str = QUEUED
    run_id: str | None = None
    baseline_run_id: str | None = None
    input_fingerprint: str | None = None
    message: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def path(self) -> Path:
        return JOBS / f"{self.job_id}.json"

    def save(self) -> "Job":
        JOBS.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        os.replace(tmp, self.path)          # atomic, so a reader never sees a half file
        return self


def new_job(project_id: str, mode: str, year: int, capacities: dict | None,
            baseline_run_id: str | None = None) -> Job:
    return Job(job_id=uuid.uuid4().hex[:12], project_id=project_id, mode=mode, year=year,
               capacities=capacities, baseline_run_id=baseline_run_id).save()


def load(job_id: str) -> Job | None:
    p = JOBS / f"{job_id}.json"
    if not p.exists():
        return None
    return Job(**json.loads(p.read_text()))


def all_jobs() -> list[Job]:
    JOBS.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(JOBS.glob("*.json"), key=lambda q: q.stat().st_mtime, reverse=True):
        try:
            out.append(Job(**json.loads(p.read_text())))
        except Exception:
            continue
    return out


def claim_next() -> Job | None:
    """Take the oldest queued job. Single worker by design; the atomic write is what
    keeps a partially-claimed job from being visible."""
    for j in sorted(all_jobs(), key=lambda x: x.created_at):
        if j.status == QUEUED:
            j.status = RUNNING
            j.started_at = time.time()
            return j.save()
    return None


def run_dir(run_id: str) -> Path:
    return RUNS / run_id
