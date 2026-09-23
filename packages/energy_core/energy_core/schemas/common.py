"""Units, provenance and the vocabulary the rest of the package agrees on."""
from __future__ import annotations

from enum import Enum

DT_HOURS = 0.25
BLOCKS_PER_DAY = 96
BLOCKS_NON_LEAP = 35_040
BLOCKS_LEAP = 35_136


class Provenance(str, Enum):
    """Where a number came from. Carried on every dataset and shown in the UI, because
    a synthetic profile must never be mistaken for a metered one."""

    MEASURED = "measured"          # uploaded from site metering
    USER_ASSUMPTION = "user"       # typed in by the user
    SYNTHETIC = "synthetic"        # generated example data
    DERIVED = "derived"            # computed from other inputs
    VENDOR_QUOTE = "vendor_quote"


class QualityFlag(str, Enum):
    OK = "ok"
    INTERPOLATED = "interpolated"  # e.g. hourly upload disaggregated to 15 minutes
    GAP_FILLED = "gap_filled"
    OUT_OF_RANGE = "out_of_range"
    DUPLICATE_DROPPED = "duplicate_dropped"


class Technology(str, Enum):
    SOLAR_ONSITE = "solar_onsite"
    SOLAR_REMOTE = "solar_remote"
    WIND_REMOTE = "wind_remote"
    BESS = "bess"


class SupplyRoute(str, Enum):
    """How energy reaches the site. Each route carries its own charge stack and its own
    eligibility rules, and they share the physical interconnection."""

    UTILITY = "utility"        # DISCOM supply on the retail tariff
    OPEN_ACCESS = "open_access"  # wheeled from a remote generator
    IEX = "iex"                # exchange purchase
    ONSITE = "onsite"          # behind the meter, no wheeling


class DemandBasis(str, Enum):
    KW = "kW"
    KVA = "kVA"


class SolveStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    FAILED = "failed"
    TIME_LIMIT = "time_limit"


class ValidationStatus(str, Enum):
    """Deliberately separate from SolveStatus: a solve that succeeded is not certified
    until the independent validator has recomputed it."""

    NOT_RUN = "not_run"
    CERTIFIED = "certified"
    FAILED = "failed"
    CERTIFIED_INCUMBENT = "certified_incumbent"  # feasible, validated, but gap > 0


class Mode(str, Enum):
    FIND_OPTIMUM = "find_optimum"
    MANUAL = "manual"
