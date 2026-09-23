"""Command line entry point. The solver runs here with no API and no browser involved."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from . import MODEL_VERSION
from .schemas.common import Mode
from .optimization import build_spec, solve_year
from .schemas.results import CapacityResult, CostLedger
from .validation import Tolerances, certify

ROOT = Path(__file__).resolve().parents[3]


def _load(year: int) -> tuple:
    sys.path.insert(0, str(ROOT / "datasets" / "synthetic"))
    from project import seeded_project                       # noqa: E402
    pq = ROOT / "datasets" / "synthetic" / f"industrial_{year}.parquet"
    if not pq.exists():
        raise SystemExit(f"missing dataset {pq}; run datasets/synthetic/generator.py first")
    return seeded_project(year), pd.read_parquet(pq)


def cmd_optimise(a) -> int:
    inputs, frame = _load(a.year)
    caps = None
    mode = Mode.FIND_OPTIMUM
    if a.manual:
        mode = Mode.MANUAL
        caps = json.loads(a.manual)
    spec = build_spec(inputs, frame, a.year, mode=mode, fixed_capacities=caps,
                      model_version=MODEL_VERSION)
    art = solve_year(spec, time_limit_s=a.time_limit, mip_gap=a.mip_gap, stream=a.stream)
    out = {
        "mode": mode.value,
        "status": art.outcome.status.value,
        "objective_inr_year": art.outcome.objective,
        "best_bound": art.outcome.best_bound,
        "gap": art.outcome.gap,
        "wall_seconds": round(art.outcome.wall_seconds, 2),
        "variables": art.outcome.n_variables,
        "constraints": art.outcome.n_constraints,
        "binaries": art.outcome.n_binaries,
        "refinement_rounds": art.refinement_rounds,
        "input_fingerprint": spec.fingerprint(),
        "model_version": MODEL_VERSION,
        "fixed_capacities": caps,
        "capacities": art.capacities.model_dump(),
        "ledger": art.ledger.model_dump(),
    }
    print(json.dumps(out, indent=2, default=float))
    if not a.no_certify and not art.dispatch.empty:
        rep = certify(spec, art.capacities, art.dispatch, art.ledger,
                      solver_objective=art.outcome.objective, status=art.outcome.status,
                      gap=art.outcome.gap, run_id=a.out or "run", tol=Tolerances(),
                      solver_version=art.outcome.solver_version)
        out["validation"] = _report_dict(rep)
    if a.out and not art.dispatch.empty:
        d = Path(a.out); d.mkdir(parents=True, exist_ok=True)
        art.dispatch.to_parquet(d / "dispatch.parquet", index=False)
        art.monthly.to_csv(d / "monthly.csv", index=False)
        (d / "summary.json").write_text(json.dumps(
            {k: v for k, v in out.items() if k != "validation"}, indent=2, default=float))
        if "validation" in out:
            (d / "validation.json").write_text(json.dumps(out["validation"], indent=2, default=float))
        print(f"\nwrote {d}/dispatch.parquet ({len(art.dispatch):,} blocks)", file=sys.stderr)
    return 0 if art.outcome.objective is not None else 1


def _report_dict(rep) -> dict:
    return {
        "validation_status": rep.status.value,
        "coverage": rep.coverage_statement,
        "checks_run": rep.checks_run,
        "material_issues": [i.model_dump(mode="json") for i in rep.material_issues],
        "worst_residuals": dict(sorted(rep.max_residual.items(),
                                       key=lambda kv: -kv[1])[:8]),
        "tolerances": rep.tolerances,
        "input_hash": rep.input_hash,
        "model_version": rep.model_version,
    }


def cmd_certify(a) -> int:
    """Re-check a saved run from the immutable inputs and the exported decisions.

    Deliberately reachable without re-solving: the point of the validator is that it
    needs nothing from the solver except the numbers it wrote down.
    """
    d = Path(a.run)
    summary = json.loads((d / "summary.json").read_text())
    dispatch = pd.read_parquet(d / "dispatch.parquet")
    inputs, frame = _load(a.year)
    mode = Mode(summary["mode"])
    fixed = summary.get("fixed_capacities")
    spec = build_spec(inputs, frame, a.year, mode=mode, fixed_capacities=fixed,
                      model_version=summary.get("model_version", MODEL_VERSION))
    if spec.fingerprint() != summary["input_fingerprint"]:
        print(json.dumps({"validation_status": "failed",
                          "reason": "input fingerprint does not match the saved run"}, indent=2))
        return 1
    caps = CapacityResult(**summary["capacities"])
    ledger = CostLedger(**summary["ledger"])
    from .schemas.common import SolveStatus
    rep = certify(spec, caps, dispatch, ledger,
                  solver_objective=summary["objective_inr_year"],
                  status=SolveStatus(summary["status"]), gap=summary.get("gap"),
                  run_id=d.name, tol=Tolerances())
    (d / "validation.json").write_text(rep.model_dump_json(indent=2))
    print(json.dumps(_report_dict(rep), indent=2, default=float))
    return 0 if rep.status.value.startswith("certified") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="energy_core", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("optimise", help="solve one operating year")
    o.add_argument("--year", type=int, default=2026)
    o.add_argument("--manual", type=str, default=None,
                   help='JSON capacities to fix, e.g. \'{"solar_oa_mw": 10}\'')
    o.add_argument("--time-limit", type=float, default=900.0)
    o.add_argument("--mip-gap", type=float, default=1e-4)
    o.add_argument("--out", type=str, default=None)
    o.add_argument("--stream", action="store_true")
    o.add_argument("--no-certify", action="store_true")
    o.set_defaults(func=cmd_optimise)
    c = sub.add_parser("certify", help="independently re-check a saved run")
    c.add_argument("--run", type=str, required=True)
    c.add_argument("--year", type=int, default=2026)
    c.set_defaults(func=cmd_certify)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
