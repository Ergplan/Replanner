"""Effective-dated retail billing.

Time-of-day rules are written in local clock time because that is how a tariff order
writes them; the canonical index is UTC, so every rule is resolved against the local
timestamp derived from the project's IANA timezone. Demand charges are billed on a
monthly measured peak in local billing months, not on an annual average.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..schemas.common import DT_HOURS, DemandBasis
from ..schemas.domain import OpenAccessCharges, TariffSchedule


@dataclass
class TariffResolution:
    """Per-block rates and the month grouping the demand charge is billed on."""

    energy_inr_per_mwh: np.ndarray     # retail energy rate, INR/MWh
    rule_id: np.ndarray                # which rule applied, for audit
    month_index: np.ndarray            # 0-based billing month of each block
    n_months: int
    month_labels: list[str]
    demand_rate_inr_per_mw_month: float
    min_billable_demand_mw: float
    duty_frac: float
    export_inr_per_mwh: float

    def month_mask(self, m: int) -> np.ndarray:
        return self.month_index == m


class TariffEngine:
    """Resolves a schedule onto an aligned frame. Nothing here reads a hardcoded rate:
    every number comes from the TariffSchedule the user supplied."""

    def __init__(self, schedule: TariffSchedule, open_access: OpenAccessCharges | None = None,
                 timezone: str = "Asia/Kolkata"):
        self.schedule = schedule
        self.open_access = open_access or OpenAccessCharges()
        self.timezone = timezone

    # -- retail ------------------------------------------------------------------
    def resolve(self, timestamps_utc: pd.DatetimeIndex) -> TariffResolution:
        local = pd.DatetimeIndex(timestamps_utc).tz_convert(self.timezone)
        n = len(local)
        rate = np.full(n, np.nan)
        rule_id = np.empty(n, dtype=object)

        month = local.month.to_numpy()
        hour = local.hour.to_numpy()
        weekday = local.dayofweek.to_numpy() < 5
        day = local.normalize()

        # Later-effective rules win, so apply in effective-date order and let the last
        # matching rule overwrite. A rule with no end date runs forever.
        for r in sorted(self.schedule.rules, key=lambda x: x.effective_from):
            eff = pd.Timestamp(r.effective_from, tz=self.timezone)
            end = pd.Timestamp(r.effective_to, tz=self.timezone) if r.effective_to else None
            m = np.asarray(day >= eff)
            if end is not None:
                m &= np.asarray(day <= end)
            m &= np.isin(month, r.months) & np.isin(hour, r.hours)
            if r.weekdays_only:
                m &= weekday
            rate[m] = r.energy_inr_per_kwh * 1000.0        # INR/kWh -> INR/MWh
            rule_id[m] = r.rule_id

        if np.isnan(rate).any():
            k = int(np.isnan(rate).sum())
            raise ValueError(
                f"tariff schedule {self.schedule.schedule_id!r} leaves {k} of {n} blocks "
                "uncovered; every block must resolve to exactly one effective rule")

        # Billing months are local calendar months over the operating year.
        ym = local.year.to_numpy() * 100 + month
        uniq, midx = np.unique(ym, return_inverse=True)
        labels = [f"{u // 100}-{u % 100:02d}" for u in uniq]

        # kVA billing converts the measured kW peak at the declared power factor, so the
        # rate is expressed per MW of measured demand once here and never again.
        pf = self.schedule.power_factor
        per_unit = self.schedule.demand_charge_inr_per_unit_month * 1000.0   # per MW-ish
        if self.schedule.demand_basis is DemandBasis.KVA:
            per_unit /= pf

        return TariffResolution(
            energy_inr_per_mwh=rate,
            rule_id=rule_id,
            month_index=midx,
            n_months=len(uniq),
            month_labels=labels,
            demand_rate_inr_per_mw_month=per_unit,
            min_billable_demand_mw=(self.schedule.ratchet_frac_of_contract
                                    * self.schedule.contract_demand_mw),
            duty_frac=self.schedule.duty_frac,
            export_inr_per_mwh=self.schedule.export_compensation_inr_per_kwh * 1000.0,
        )

    # -- open access ---------------------------------------------------------------
    @property
    def oa_charge_inr_per_mwh(self) -> float:
        """The wheeled-energy charge stack, applied to delivered energy."""
        return self.open_access.total_inr_per_kwh * 1000.0

    def annual_energy_cost(self, import_mw: np.ndarray, res: TariffResolution) -> float:
        return float(np.sum(import_mw * DT_HOURS * res.energy_inr_per_mwh))

    def monthly_billing_demand(self, import_mw: np.ndarray, res: TariffResolution) -> np.ndarray:
        """The measured monthly peak, floored by any ratchet or minimum-billing rule."""
        peaks = np.array([import_mw[res.month_mask(m)].max() if res.month_mask(m).any() else 0.0
                          for m in range(res.n_months)])
        return np.maximum(peaks, res.min_billable_demand_mw)

    def annual_demand_cost(self, import_mw: np.ndarray, res: TariffResolution) -> float:
        return float(np.sum(self.monthly_billing_demand(import_mw, res))
                     * res.demand_rate_inr_per_mw_month)
