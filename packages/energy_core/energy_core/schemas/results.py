"""What a run produces. Kept separate from the domain inputs so a result can never be
confused for an assumption."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .common import Mode, SolveStatus, ValidationStatus


class CostLedger(BaseModel):
    """Every component of the equivalent annual cost, in INR/year. These must sum to
    `total_annual_cost`, and the independent validator re-derives each one."""

    annualised_capex_new: float = 0.0
    fixed_om: float = 0.0
    variable_om: float = 0.0
    utility_energy: float = 0.0
    utility_demand: float = 0.0
    duty: float = 0.0
    open_access_charges: float = 0.0
    market_purchase: float = 0.0
    market_transaction: float = 0.0
    battery_wear: float = 0.0
    export_revenue: float = 0.0        # negative, a credit
    total_annual_cost: float = 0.0

    def components(self) -> dict[str, float]:
        return {k: v for k, v in self.model_dump().items() if k != "total_annual_cost"}

    def recompute_total(self) -> float:
        return sum(self.components().values())


class CapacityResult(BaseModel):
    solar_onsite_mw: float = 0.0
    solar_remote_mw: float = 0.0
    wind_remote_mw: float = 0.0
    bess_power_mw: float = 0.0
    bess_energy_mwh: float = 0.0
    existing_solar_onsite_mw: float = 0.0
    existing_bess_power_mw: float = 0.0
    existing_bess_energy_mwh: float = 0.0

    @property
    def bess_duration_h(self) -> float | None:
        p = self.bess_power_mw + self.existing_bess_power_mw
        e = self.bess_energy_mwh + self.existing_bess_energy_mwh
        return (e / p) if p > 1e-9 else None


class DispatchFrame(BaseModel):
    """Metadata only — the per-block table is Parquet on disk, addressed by path."""

    run_id: str
    operating_year: int
    n_blocks: int
    parquet_path: str
    columns: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    check: str
    severity: str                  # "material" or "roundoff"
    timestamp_utc: datetime | None = None
    block_index: int | None = None
    residual: float = 0.0
    tolerance: float = 0.0
    units: str = ""
    detail: str = ""


class ValidationReport(BaseModel):
    run_id: str
    status: ValidationStatus = ValidationStatus.NOT_RUN
    blocks_checked: int = 0
    blocks_expected: int = 0
    checks_run: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    max_residual: dict[str, float] = Field(default_factory=dict)
    tolerances: dict[str, float] = Field(default_factory=dict)
    input_hash: str = ""
    model_version: str = ""
    solver_version: str = ""
    generated_at: datetime | None = None

    @property
    def material_issues(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "material"]

    @property
    def coverage_statement(self) -> str:
        """The only place allowed to render the '35,040 / 35,040' claim, and only from
        counts the validator actually walked."""
        return f"{self.blocks_checked:,} / {self.blocks_expected:,} blocks checked"


class SolveRun(BaseModel):
    run_id: str
    project_id: str
    scenario_id: str
    mode: Mode
    status: SolveStatus = SolveStatus.QUEUED
    validation: ValidationStatus = ValidationStatus.NOT_RUN
    model_version: str = ""
    input_fingerprint: str = ""
    baseline_run_id: str | None = None
    capacities: CapacityResult = CapacityResult()
    ledger: CostLedger = CostLedger()
    objective: float | None = None
    best_bound: float | None = None
    gap: float | None = None
    solver: str = ""
    solver_version: str = ""
    wall_seconds: float = 0.0
    n_variables: int = 0
    n_constraints: int = 0
    message: str = ""
    created_at: datetime | None = None
    dispatch: DispatchFrame | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_certified_optimum(self) -> bool:
        """Succeeded is not certified, and certified-with-a-gap is not the optimum."""
        return (self.status is SolveStatus.SUCCEEDED
                and self.validation is ValidationStatus.CERTIFIED
                and (self.gap is None or self.gap <= 1e-6))
