"""A hand-sized RunSpec, so the model can be checked against arithmetic done on paper."""
from __future__ import annotations

import numpy as np
import pandas as pd

from energy_core.schemas.common import DT_HOURS, Mode
from energy_core.optimization.spec import BatterySpec, GenSpec, RunSpec


def tiny_spec(n: int = 96, *, load_mw=1.0, tariff_inr_per_mwh=8000.0, demand_rate=0.0,
              gens=None, battery=None, import_limit=10.0, export_limit=0.0,
              iex_buy=None, market=False, month_index=None, duty=0.0,
              mode=Mode.FIND_OPTIMUM, fixed=None) -> RunSpec:
    ts = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC", name="timestamp_utc")
    arr = lambda v: np.full(n, float(v))
    load = arr(load_mw) if np.isscalar(load_mw) else np.asarray(load_mw, dtype=float)
    tar = arr(tariff_inr_per_mwh) if np.isscalar(tariff_inr_per_mwh) else np.asarray(
        tariff_inr_per_mwh, dtype=float)
    mi = np.zeros(n, dtype=int) if month_index is None else np.asarray(month_index)
    return RunSpec(
        project_id="tiny", operating_year=2026, timezone="UTC", n=n, dt=DT_HOURS,
        timestamps_utc=ts, load_mw=load, grid_available=arr(1.0),
        energy_inr_per_mwh=tar, tariff_rule_id=np.array(["r"] * n, dtype=object),
        month_index=mi, n_months=int(mi.max()) + 1, month_labels=["m"] * (int(mi.max()) + 1),
        demand_rate_inr_per_mw_month=demand_rate, min_billable_demand_mw=0.0,
        duty_frac=duty, export_inr_per_mwh=0.0,
        iex_buy_inr_per_mwh=arr(0.0) if iex_buy is None else np.asarray(iex_buy, dtype=float),
        iex_sell_inr_per_mwh=arr(0.0),
        market_enabled=market, market_buy_enabled=market, market_sell_enabled=False,
        market_buy_limit_mw=10.0 if market else 0.0, market_sell_limit_mw=0.0,
        market_txn_inr_per_mwh=0.0, market_oa_applies=False, oa_charge_inr_per_mwh=0.0,
        import_limit_mw=import_limit, export_limit_mw=export_limit,
        allow_export=export_limit > 0,
        generation=gens or [], battery=battery or no_battery(),
        existing_fixed_om_inr_year=0.0, mode=mode, fixed_capacities=fixed,
        model_version="test",
    )


def no_battery() -> BatterySpec:
    return BatterySpec(
        enabled=False, existing_mw=0.0, existing_mwh=0.0, min_mw=0.0, max_mw=0.0,
        min_mwh=0.0, max_mwh=0.0, annuity_inr_per_mw_year=0.0, annuity_inr_per_mwh_year=0.0,
        fixed_om_inr_per_mw_year=0.0, eta_charge=1.0, eta_discharge=1.0,
        retention_per_block=1.0, soc_min_frac=0.0, soc_max_frac=1.0, max_c_rate=1.0,
        aux_frac_of_power=0.0, wear_inr_per_mwh_discharged=0.0,
        warranty_throughput_efc=6000.0, terminal_rule="cyclic", initial_soc_frac=0.0,
        allow_grid_charging=True)


def battery(power=1.0, energy=4.0, eta_c=1.0, eta_d=1.0, wear=0.0, annuity_mw=0.0,
            annuity_mwh=0.0, retention=1.0, soc_min=0.0, soc_max=1.0,
            grid_charging=True, aux=0.0, c_rate=1.0, max_efc=None) -> BatterySpec:
    return BatterySpec(
        enabled=True, existing_mw=0.0, existing_mwh=0.0, min_mw=0.0, max_mw=power,
        min_mwh=0.0, max_mwh=energy, annuity_inr_per_mw_year=annuity_mw,
        annuity_inr_per_mwh_year=annuity_mwh, fixed_om_inr_per_mw_year=0.0,
        eta_charge=eta_c, eta_discharge=eta_d, retention_per_block=retention,
        soc_min_frac=soc_min, soc_max_frac=soc_max, max_c_rate=c_rate, aux_frac_of_power=aux,
        wear_inr_per_mwh_discharged=wear, warranty_throughput_efc=6000.0,
        terminal_rule="cyclic", initial_soc_frac=0.0, allow_grid_charging=grid_charging,
        max_efc_per_year=max_efc)


def solar(profile, cap_max=10.0, annuity=0.0, loss=0.0, existing=0.0,
          key="pv", cap_key="solar_onsite_mw", wheeled=False, vom=0.0,
          cap_min=0.0, step=None) -> GenSpec:
    return GenSpec(key=key, route="onsite", cap_key=cap_key,
                   profile=np.asarray(profile, dtype=float), delivery_factor=1.0 - loss,
                   existing_mw=existing, min_mw=cap_min, max_mw=cap_max,
                   annuity_inr_per_mw_year=annuity, fixed_om_inr_per_mw_year=0.0,
                   variable_om_inr_per_mwh=vom, wheeled=wheeled, step_mw=step)
