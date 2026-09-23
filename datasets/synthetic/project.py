"""The seeded example project.

Every rate here is illustrative. The open-access charge stack in particular is written as
configurable inputs precisely because it is set by state commission orders and changes;
none of these are current statutory values and none should be quoted as such.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "energy_core"))
from energy_core.schemas.common import DemandBasis, SupplyRoute  # noqa: E402
from energy_core.schemas.domain import (  # noqa: E402
    AssetOption, BatteryOption, BatteryTechnical, ExistingAsset, FinancialAssumptions,
    MarketTerms, OpenAccessCharges, Project, ProjectInputs, SiteLimits, TariffRule,
    TariffSchedule,
)

CR = 10_000_000.0          # one crore, so capital costs read the way they are quoted


def seeded_project(year: int = 2026) -> ProjectInputs:
    tariff = TariffSchedule(
        schedule_id="mh-ht-industrial-illustrative",
        demand_basis=DemandBasis.KVA,
        power_factor=0.95,
        demand_charge_inr_per_unit_month=475.0,
        contract_demand_mw=13.0,
        ratchet_frac_of_contract=0.75,
        duty_frac=0.093,
        export_compensation_inr_per_kwh=0.0,
        rules=[
            TariffRule(rule_id="normal", effective_from=date(2020, 1, 1),
                       energy_inr_per_kwh=8.44, label="normal hours"),
            TariffRule(rule_id="night", effective_from=date(2020, 1, 1),
                       hours=[22, 23, 0, 1, 2, 3, 4, 5],
                       energy_inr_per_kwh=6.33, label="off peak"),
            TariffRule(rule_id="evening_peak", effective_from=date(2020, 1, 1),
                       hours=[18, 19, 20, 21],
                       energy_inr_per_kwh=10.128, label="evening peak"),
        ],
    )

    options = [
        AssetOption(
            option_id="solar_roof", technology="solar_onsite", route=SupplyRoute.ONSITE,
            max_mw=8.0, capex_inr_per_mw=3.5 * CR, fixed_om_inr_per_mw_year=0.035 * CR,
            life_years=25, degradation_pct_per_year=0.005, delivery_loss_pct=0.0,
            profile_key="solar_onsite_cf"),
        AssetOption(
            option_id="solar_oa", technology="solar_remote", route=SupplyRoute.OPEN_ACCESS,
            max_mw=60.0, capex_inr_per_mw=3.0 * CR, fixed_om_inr_per_mw_year=0.030 * CR,
            life_years=25, degradation_pct_per_year=0.005, delivery_loss_pct=0.05,
            profile_key="solar_remote_cf"),
        AssetOption(
            option_id="wind_oa", technology="wind_remote", route=SupplyRoute.OPEN_ACCESS,
            max_mw=60.0, capex_inr_per_mw=6.5 * CR, fixed_om_inr_per_mw_year=0.100 * CR,
            life_years=25, degradation_pct_per_year=0.002, delivery_loss_pct=0.05,
            profile_key="wind_remote_cf"),
    ]

    battery = BatteryOption(
        option_id="bess", max_power_mw=25.0, max_energy_mwh=120.0,
        capex_inr_per_mw=1.2 * CR, capex_inr_per_mwh=1.8 * CR,
        fixed_om_inr_per_mw_year=0.020 * CR,
        power_life_years=20, energy_life_years=15,
        technical=BatteryTechnical(
            soc_min_frac=0.05, soc_max_frac=0.95, eta_charge=0.96, eta_discharge=0.96,
            self_discharge_pct_per_day=0.02, max_c_rate=0.5, aux_load_frac_of_power=0.01,
            warranty_throughput_mwh_per_mwh=6000.0, terminal_soc_rule="cyclic",
            allow_grid_charging=True, allow_export=False),
    )

    return ProjectInputs(
        project=Project(
            project_id="seed-industrial-mh", name="Seeded industrial site (illustrative)",
            timezone="Asia/Kolkata", base_year=year, operating_years=[year],
            site=SiteLimits(import_limit_mw=15.0, export_limit_mw=0.0,
                            roof_area_mw_cap=8.0, land_mw_cap=0.0)),
        existing_assets=[
            ExistingAsset(asset_id="roof-legacy", technology="solar_onsite", capacity_mw=1.0,
                          commissioned=date(2022, 4, 1),
                          fixed_om_inr_per_mw_year=0.035 * CR),
        ],
        asset_options=options,
        battery=battery,
        tariff=tariff,
        open_access=OpenAccessCharges(
            transmission_inr_per_kwh=0.35, wheeling_inr_per_kwh=0.55,
            cross_subsidy_surcharge_inr_per_kwh=1.62,
            additional_surcharge_inr_per_kwh=0.90, scheduling_inr_per_kwh=0.05),
        market=MarketTerms(enabled=True, buy_enabled=True, sell_enabled=False,
                           buy_limit_mw=10.0, transaction_inr_per_kwh=0.07,
                           oa_charges_apply=True),
        finance=FinancialAssumptions(discount_rate=0.10, study_period_years=20,
                                     base_year=year),
    )
