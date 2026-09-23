"""The domain contract. Every number a user can set lives here, with its unit in the
field name or its description, so that nothing downstream has to guess."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .common import DemandBasis, Mode, Provenance, SupplyRoute


class SiteLimits(BaseModel):
    """Physical limits at the point of common coupling. Utility, open-access and market
    imports all flow through the same wires, so they share these numbers."""

    import_limit_mw: float = Field(gt=0, description="Sanctioned import at the PCC, MW")
    export_limit_mw: float = Field(default=0.0, ge=0, description="Sanctioned export, MW")
    roof_area_mw_cap: float = Field(default=0.0, ge=0, description="Roof-limited onsite solar, MWp")
    land_mw_cap: float = Field(default=0.0, ge=0, description="Ground-mount limit, MWp")


class ExistingAsset(BaseModel):
    """Already built and already paid for. Its capex is sunk and must never be charged
    again as a new investment; only its fixed O&M and its physics carry forward."""

    asset_id: str
    technology: str
    capacity_mw: float = Field(ge=0)
    energy_mwh: float = Field(default=0.0, ge=0, description="For storage only")
    commissioned: date
    retires: date | None = None
    fixed_om_inr_per_mw_year: float = Field(default=0.0, ge=0)
    route: SupplyRoute = SupplyRoute.ONSITE


class AssetOption(BaseModel):
    """A technology the optimiser is allowed to buy."""

    option_id: str
    technology: str
    route: SupplyRoute
    enabled: bool = True
    min_mw: float = Field(default=0.0, ge=0)
    max_mw: float = Field(default=0.0, ge=0)
    step_mw: float | None = Field(default=None, gt=0, description="Discrete sizes if set")
    capex_inr_per_mw: float = Field(default=0.0, ge=0)
    fixed_om_inr_per_mw_year: float = Field(default=0.0, ge=0)
    variable_om_inr_per_mwh: float = Field(default=0.0, ge=0)
    life_years: int = Field(default=25, gt=0)
    degradation_pct_per_year: float = Field(default=0.0, ge=0, le=0.2)
    delivery_loss_pct: float = Field(
        default=0.0, ge=0, lt=1,
        description="Wheeling and transmission loss for remote sources. Onsite is 0.",
    )
    profile_key: str = Field(description="Which capacity-factor series this option uses")

    @model_validator(mode="after")
    def _bounds(self):
        if self.max_mw < self.min_mw:
            raise ValueError(f"{self.option_id}: max_mw < min_mw")
        return self


class BatteryTechnical(BaseModel):
    """Kept apart from the cost option because these are physics, and because depth of
    discharge and efficiency are each applied exactly once, here."""

    soc_min_frac: float = Field(default=0.05, ge=0, lt=1)
    soc_max_frac: float = Field(default=0.95, gt=0, le=1)
    eta_charge: float = Field(default=0.96, gt=0, le=1, description="One-way, AC to DC")
    eta_discharge: float = Field(default=0.96, gt=0, le=1, description="One-way, DC to AC")
    self_discharge_pct_per_day: float = Field(default=0.05, ge=0, lt=1)
    max_c_rate: float = Field(default=0.5, gt=0, description="Power MW per MWh installed")
    aux_load_frac_of_power: float = Field(
        default=0.01, ge=0, lt=0.2, description="HVAC and BMS, as a fraction of rated PCS MW")
    calendar_fade_pct_per_year: float = Field(default=0.02, ge=0, lt=0.5)
    cycle_fade_pct_per_efc: float = Field(default=0.0025e-2, ge=0)
    warranty_throughput_mwh_per_mwh: float = Field(default=6000.0, gt=0, description="EFC over life")
    eol_soh_frac: float = Field(default=0.70, gt=0, lt=1)
    terminal_soc_rule: Literal["cyclic", "fixed"] = "cyclic"
    initial_soc_frac: float = Field(default=0.5, ge=0, le=1)
    allow_grid_charging: bool = True
    allow_export: bool = False

    @model_validator(mode="after")
    def _soc(self):
        if self.soc_max_frac <= self.soc_min_frac:
            raise ValueError("soc_max_frac must exceed soc_min_frac")
        return self


class BatteryOption(BaseModel):
    """Power and energy are bought separately and priced separately: INR/MW for the
    conversion block, INR/MWh for the cells."""

    option_id: str = "bess"
    enabled: bool = True
    min_power_mw: float = Field(default=0.0, ge=0)
    max_power_mw: float = Field(default=0.0, ge=0)
    min_energy_mwh: float = Field(default=0.0, ge=0)
    max_energy_mwh: float = Field(default=0.0, ge=0)
    capex_inr_per_mw: float = Field(default=0.0, ge=0, description="PCS, balance of plant")
    capex_inr_per_mwh: float = Field(default=0.0, ge=0, description="Cells and enclosure")
    fixed_om_inr_per_mw_year: float = Field(default=0.0, ge=0)
    variable_om_inr_per_mwh_throughput: float = Field(default=0.0, ge=0)
    power_life_years: int = Field(default=20, gt=0)
    energy_life_years: int = Field(default=15, gt=0)
    technical: BatteryTechnical = BatteryTechnical()


class ExpansionPhase(BaseModel):
    """Load growth arrives either as an additive profile or as a scaling rule, never
    both for the same phase, so growth cannot be applied twice."""

    phase_id: str
    commissioning: date
    additive_profile_key: str | None = None
    load_scale_factor: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _one_mechanism(self):
        if (self.additive_profile_key is None) == (self.load_scale_factor is None):
            raise ValueError(
                f"{self.phase_id}: set exactly one of additive_profile_key or load_scale_factor")
        return self


class TariffRule(BaseModel):
    """One effective-dated retail rule. Hours are local clock hours in the project
    timezone, months are 1-12, both inclusive ranges."""

    rule_id: str
    effective_from: date
    effective_to: date | None = None
    months: list[int] = Field(default_factory=lambda: list(range(1, 13)))
    hours: list[int] = Field(default_factory=lambda: list(range(0, 24)))
    weekdays_only: bool = False
    energy_inr_per_kwh: float = Field(ge=0)
    label: str = ""


class TariffSchedule(BaseModel):
    """Retail supply. Demand charges are billed on a monthly measured peak, on the
    stated basis; kVA billing requires an explicit power factor."""

    schedule_id: str
    rules: list[TariffRule]
    demand_basis: DemandBasis = DemandBasis.KVA
    power_factor: float = Field(default=0.95, gt=0, le=1)
    demand_charge_inr_per_unit_month: float = Field(default=0.0, ge=0)
    contract_demand_mw: float = Field(gt=0)
    ratchet_frac_of_contract: float = Field(
        default=0.0, ge=0, le=1, description="Minimum billable demand as a fraction of contract")
    duty_frac: float = Field(default=0.0, ge=0, lt=1, description="Electricity duty on energy+demand")
    export_compensation_inr_per_kwh: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _rules(self):
        if not self.rules:
            raise ValueError("a tariff schedule needs at least one rule")
        return self


class OpenAccessCharges(BaseModel):
    """The charge stack on wheeled energy. Every component is configurable because none
    of these are constants — they are set by state commissions and change by order."""

    transmission_inr_per_kwh: float = Field(default=0.0, ge=0)
    wheeling_inr_per_kwh: float = Field(default=0.0, ge=0)
    cross_subsidy_surcharge_inr_per_kwh: float = Field(default=0.0, ge=0)
    additional_surcharge_inr_per_kwh: float = Field(default=0.0, ge=0)
    scheduling_inr_per_kwh: float = Field(default=0.0, ge=0)
    banking_enabled: bool = False
    effective_from: date | None = None

    @property
    def total_inr_per_kwh(self) -> float:
        return (self.transmission_inr_per_kwh + self.wheeling_inr_per_kwh
                + self.cross_subsidy_surcharge_inr_per_kwh
                + self.additional_surcharge_inr_per_kwh + self.scheduling_inr_per_kwh)


class MarketTerms(BaseModel):
    """Exchange purchase and sale. Prices come from a forecast series, never from here."""

    enabled: bool = False
    buy_enabled: bool = True
    sell_enabled: bool = False
    buy_limit_mw: float = Field(default=0.0, ge=0)
    sell_limit_mw: float = Field(default=0.0, ge=0)
    transaction_inr_per_kwh: float = Field(default=0.0, ge=0)
    oa_charges_apply: bool = True


class FinancialAssumptions(BaseModel):
    discount_rate: float = Field(default=0.10, ge=0, lt=1)
    basis: Literal["real", "nominal"] = "real"
    study_period_years: int = Field(default=20, gt=0)
    base_year: int = Field(default=2026)
    opex_escalation: float = Field(default=0.0, ge=-0.2, lt=0.5)
    salvage: Literal["straight_line", "none"] = "straight_line"
    currency: str = "INR"


class CapacityDecision(BaseModel):
    """The only thing allowed to differ between a Mode A optimum and a Mode B scenario."""

    solar_onsite_mw: float = Field(default=0.0, ge=0)
    solar_remote_mw: float = Field(default=0.0, ge=0)
    wind_remote_mw: float = Field(default=0.0, ge=0)
    bess_power_mw: float = Field(default=0.0, ge=0)
    bess_energy_mwh: float = Field(default=0.0, ge=0)

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class Scenario(BaseModel):
    scenario_id: str
    mode: Mode
    capacities: CapacityDecision | None = None   # required for Mode.MANUAL
    label: str = ""

    @model_validator(mode="after")
    def _manual_needs_caps(self):
        if self.mode is Mode.MANUAL and self.capacities is None:
            raise ValueError("a manual scenario must carry capacities")
        return self


class Project(BaseModel):
    project_id: str
    name: str
    timezone: str = "Asia/Kolkata"
    currency: str = "INR"
    base_year: int = 2026
    operating_years: list[int] = Field(default_factory=lambda: [2026])
    site: SiteLimits
    provenance: Provenance = Provenance.SYNTHETIC


class ProjectInputs(BaseModel):
    """One immutable bundle. Its hash is the input fingerprint that a Mode B comparison
    is pinned to; change anything but capacities and the comparison is stale."""

    project: Project
    existing_assets: list[ExistingAsset] = Field(default_factory=list)
    asset_options: list[AssetOption] = Field(default_factory=list)
    battery: BatteryOption = BatteryOption()
    expansion: list[ExpansionPhase] = Field(default_factory=list)
    tariff: TariffSchedule
    open_access: OpenAccessCharges = OpenAccessCharges()
    market: MarketTerms = MarketTerms()
    finance: FinancialAssumptions = FinancialAssumptions()
