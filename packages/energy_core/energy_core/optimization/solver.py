"""HiGHS adapter.

Kept behind a thin interface for two reasons: the solver is the one component whose
behaviour is not ours to define, and status handling is where optimistic code usually
lies. A solve that stops on a time limit returns an incumbent, not an optimum, and this
module is where that distinction is made rather than lost.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pyomo.environ as pyo
from pyomo.contrib.appsi.base import TerminationCondition as TC
from pyomo.contrib.appsi.solvers.highs import Highs

from ..schemas.common import SolveStatus

_STATUS = {
    TC.optimal: SolveStatus.SUCCEEDED,
    TC.maxTimeLimit: SolveStatus.TIME_LIMIT,
    TC.maxIterations: SolveStatus.TIME_LIMIT,
    TC.infeasible: SolveStatus.INFEASIBLE,
    TC.unbounded: SolveStatus.UNBOUNDED,
    TC.infeasibleOrUnbounded: SolveStatus.INFEASIBLE,
    TC.interrupted: SolveStatus.CANCELLED,
    TC.error: SolveStatus.FAILED,
}


@dataclass
class SolveOutcome:
    status: SolveStatus
    termination: str
    objective: float | None
    best_bound: float | None
    gap: float | None
    wall_seconds: float
    n_variables: int
    n_constraints: int
    n_binaries: int
    n_integers: int = 0            # general integers, binaries excluded
    solver: str = "HiGHS"
    solver_version: str = ""
    message: str = ""

    @property
    def is_proven_optimal(self) -> bool:
        return self.status is SolveStatus.SUCCEEDED and (self.gap is None or self.gap <= 1e-6)


def _version(opt) -> str:
    """HiGHS reports its version as a tuple; the report wants a string."""
    try:
        v = opt.version()
    except Exception:
        return ""
    return ".".join(str(x) for x in v) if isinstance(v, (tuple, list)) else str(v)


def _count(model) -> tuple[int, int, int, int]:
    nv = nb = ni = 0
    for v in model.component_data_objects(pyo.Var, active=True):
        nv += 1
        if v.is_binary():
            nb += 1
        elif v.is_integer():
            ni += 1
    nc = sum(1 for _ in model.component_data_objects(pyo.Constraint, active=True))
    return nv, nc, nb, ni


def solve(model, *, time_limit_s: float | None = 900.0, mip_gap: float = 1e-4,
          threads: int | None = None, stream: bool = False) -> SolveOutcome:
    opt = Highs()
    opt.config.load_solution = False        # loaded manually so a bad status cannot leak in
    opt.config.stream_solver = stream
    if time_limit_s:
        opt.config.time_limit = float(time_limit_s)
    opt.config.mip_gap = mip_gap
    if threads:
        opt.highs_options["threads"] = int(threads)

    t0 = time.perf_counter()
    res = opt.solve(model)
    wall = time.perf_counter() - t0
    nv, nc, nb, ni = _count(model)

    status = _STATUS.get(res.termination_condition, SolveStatus.FAILED)
    obj = bound = gap = None
    if status in (SolveStatus.SUCCEEDED, SolveStatus.TIME_LIMIT):
        try:
            res.solution_loader.load_vars()
            obj = float(pyo.value(model.obj))
        except Exception as exc:                       # no feasible incumbent to load
            return SolveOutcome(SolveStatus.FAILED, str(res.termination_condition), None, None,
                                None, wall, nv, nc, nb, ni,
                                message=f"no loadable solution: {exc}")
        bound = (float(res.best_objective_bound)
                 if res.best_objective_bound is not None else None)
        if bound is not None and obj is not None and abs(obj) > 1e-9:
            gap = abs(obj - bound) / max(abs(obj), 1e-9)
        elif nb == 0 and ni == 0:
            gap = 0.0                                   # an LP solved to optimality has no gap

    return SolveOutcome(
        status=status, termination=str(res.termination_condition), objective=obj,
        best_bound=bound, gap=gap, wall_seconds=wall,
        n_variables=nv, n_constraints=nc, n_binaries=nb, n_integers=ni,
        solver_version=_version(opt),
        message="" if status is SolveStatus.SUCCEEDED else str(res.termination_condition))
