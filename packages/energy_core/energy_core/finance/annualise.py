"""Capital recovery and annualisation.

One rule governs this module: a capital item is charged to the objective exactly once,
either as an annuity or as a discounted cash flow, never as both. Debt principal is not
added on top of an annuity that already recovers the capital.
"""
from __future__ import annotations

import numpy as np


def crf(rate: float, years: int) -> float:
    """Capital recovery factor. At a zero discount rate this is straight-line, 1/n,
    which is the limit of the usual expression rather than a special case to be feared."""
    if years <= 0:
        raise ValueError("years must be positive")
    if abs(rate) < 1e-12:
        return 1.0 / years
    f = (1.0 + rate) ** years
    return rate * f / (f - 1.0)


def salvage_fraction(life_years: int, study_years: int, convention: str = "straight_line") -> float:
    """The share of an asset's cost still unconsumed at the end of the study period.

    An asset bought once and outliving the study is not fully expensed against it; an
    asset replaced partway through leaves a stub. Straight-line is the documented default.
    """
    if convention == "none" or life_years <= 0:
        return 0.0
    used = study_years % life_years
    if used == 0:
        return 0.0
    return (life_years - used) / life_years


def annualised_capex(capex: float, rate: float, life_years: int) -> float:
    """The equivalent annual cost of one capital item over its own life."""
    return capex * crf(rate, life_years)


def npv(cashflows: np.ndarray | list[float], rate: float, t0: int = 0) -> float:
    """Present value of an end-of-period cashflow series starting at period `t0`."""
    cf = np.asarray(cashflows, dtype=float)
    t = np.arange(cf.size) + t0
    return float(np.sum(cf / (1.0 + rate) ** t))


def eac_from_npv(value: float, rate: float, study_years: int) -> float:
    """Convert a whole-of-horizon present value into an equivalent annual cost, using the
    study-horizon CRF. This is the only sanctioned bridge between the multi-year cash-flow
    formulation and the single repeating operating year."""
    return value * crf(rate, study_years)
