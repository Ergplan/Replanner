"""The solve worker. Run it alongside the API:  python services/worker/worker.py"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "energy_core"))
sys.path.insert(0, str(ROOT / "datasets" / "synthetic"))

import pandas as pd  # noqa: E402

from energy_core import MODEL_VERSION  # noqa: E402
from energy_core.optimization import build_spec, solve_year  # noqa: E402
from energy_core.schemas.common import Mode  # noqa: E402
from energy_core.validation import Tolerances, certify  # noqa: E402

import jobs as J  # noqa: E402


def _inputs(year: int):
    from project import seeded_project
    pq = ROOT / "datasets" / "synthetic" / f"industrial_{year}.parquet"
    return seeded_project(year), pd.read_parquet(pq)


def execute(job: J.Job) -> None:
    inputs, frame = _inputs(job.year)
    mode = Mode(job.mode)
    spec = build_spec(inputs, frame, job.year, mode=mode,
                      fixed_capacities=job.capacities, model_version=MODEL_VERSION)
    job.input_fingerprint = spec.fingerprint()
    job.save()

    art = solve_year(spec, time_limit_s=900.0)
    run_id = f"{job.mode}-{job.job_id}"
    d = J.run_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)

    summary = {
        "run_id": run_id, "job_id": job.job_id, "project_id": job.project_id,
        "mode": job.mode, "year": job.year,
        "status": art.outcome.status.value,
        "objective_inr_year": art.outcome.objective,
        "best_bound": art.outcome.best_bound, "gap": art.outcome.gap,
        "wall_seconds": art.outcome.wall_seconds,
        "variables": art.outcome.n_variables, "constraints": art.outcome.n_constraints,
        "binaries": art.outcome.n_binaries, "integers": art.outcome.n_integers,
        "refinement_rounds": art.refinement_rounds,
        "solver": art.outcome.solver, "solver_version": art.outcome.solver_version,
        "model_version": MODEL_VERSION,
        "input_fingerprint": spec.fingerprint(),
        "baseline_run_id": job.baseline_run_id,
        "fixed_capacities": job.capacities,
        "capacities": art.capacities.model_dump(),
        "ledger": art.ledger.model_dump(),
    }
    if art.dispatch.empty:
        # An infeasible scenario is told to explain itself. The diagnostic run opens a
        # priced unserved-load slack purely to locate the shortfall; it is recorded as a
        # diagnostic, never as a design, and never carries an accepted badge.
        summary["validation_status"] = "not_run"
        summary["reason"] = art.notes.get("reason", "no solution")
        if mode is Mode.MANUAL:
            diag = solve_year(spec, time_limit_s=900.0, diagnostic=True)
            if not diag.dispatch.empty:
                summary["diagnostic"] = {
                    "ran": True,
                    "unserved_mwh": diag.notes.get("unserved_mwh"),
                    "unserved_blocks": diag.notes.get("unserved_blocks"),
                    "worst_unserved_mw": diag.notes.get("worst_unserved_mw"),
                    "windows": diag.notes.get("unserved_windows"),
                    "note": ("Diagnostic relaxation only. Load was allowed to go unserved at "
                             "a penalty price to find where this configuration cannot serve "
                             "the site. It is not a feasible design."),
                }
                diag.dispatch.to_parquet(d / "diagnostic_dispatch.parquet", index=False)
        (d / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
        job.status = J.FAILED
        job.message = summary.get("reason", "no solution")
        job.run_id, job.finished_at = run_id, time.time()
        job.save()
        return

    rep = certify(spec, art.capacities, art.dispatch, art.ledger,
                  solver_objective=art.outcome.objective, status=art.outcome.status,
                  gap=art.outcome.gap, run_id=run_id, tol=Tolerances(),
                  solver_version=art.outcome.solver_version)
    summary["validation_status"] = rep.status.value
    summary["coverage"] = rep.coverage_statement

    # Persist everything before the job is marked done, so a completed job always has
    # readable artefacts behind it.
    art.dispatch.to_parquet(d / "dispatch.parquet", index=False)
    art.monthly.to_csv(d / "monthly.csv", index=False)
    (d / "validation.json").write_text(rep.model_dump_json(indent=2))
    (d / "summary.json").write_text(json.dumps(summary, indent=2, default=float))

    job.run_id = run_id
    job.status = J.SUCCEEDED
    job.finished_at = time.time()
    job.save()


def main(poll_s: float = 0.5) -> None:
    print(f"worker up; watching {J.JOBS}", flush=True)
    while True:
        job = J.claim_next()
        if job is None:
            time.sleep(poll_s)
            continue
        print(f"solving {job.job_id} ({job.mode})", flush=True)
        try:
            execute(job)
            print(f"  -> {job.status} {job.run_id}", flush=True)
        except Exception as exc:                      # a failed job must not kill the worker
            job.status, job.message = J.FAILED, f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()
            job.save()
            print(f"  -> failed: {exc}", flush=True)


if __name__ == "__main__":
    main()
