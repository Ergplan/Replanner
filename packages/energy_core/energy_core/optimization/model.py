"""The LP/MILP builder. One builder serves both modes.

Mode A lets the capacity variables move; Mode B pins them by bounds on the backend, so a
manual scenario cannot be faked by the UI. Everything else — physics, tariffs, costs — is
literally the same code, which is what makes the two results comparable.

Sign and unit conventions, stated once:
  * power variables are MW at the site bus, energy is MWh, money is INR/year;
  * an interval contributes `power * dt` MWh, with dt = 0.25 h always;
  * round-trip efficiency is applied to energy, once, in the SOC recursion, and never to
    a price.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyomo.environ as pyo

from ..schemas.common import Mode
from .spec import RunSpec


@dataclass
class BuildInfo:
    n_binary_blocks: int
    simultaneity_blocks: list[int]
    fixed: bool


def candidate_simultaneity_blocks(spec: RunSpec) -> list[int]:
    """Blocks where the price structure could reward physically silly behaviour.

    Simultaneous import and export, or simultaneous charge and discharge, are never
    profitable when buying costs more than selling earns and the round trip loses energy.
    They become profitable at negative purchase prices. Rather than carry 70,000 binaries
    for a handful of such intervals, the binaries go only where the arithmetic allows the
    behaviour, and the independent validator then checks every block. Any violation it
    finds is fed back and the model re-solved, so the search is guarded by the check
    rather than by the assumption.
    """
    if not spec.allow_export and not (spec.market_enabled and spec.market_sell_enabled):
        return []
    buy = np.minimum(spec.energy_inr_per_mwh * (1 + spec.duty_frac),
                     spec.iex_buy_inr_per_mwh + spec.market_txn_inr_per_mwh
                     if spec.market_buy_enabled else np.inf)
    sell = np.maximum(spec.export_inr_per_mwh,
                      spec.iex_sell_inr_per_mwh if spec.market_sell_enabled else -np.inf)
    return np.flatnonzero(sell > buy - 1e-9).tolist()


def build_model(spec: RunSpec, *, force_binary_blocks: list[int] | None = None,
                diagnostic: bool = False):
    """Construct the Pyomo model for one operating year.

    `diagnostic` opens a priced unserved-load slack so an infeasible scenario can be made
    to say *where* and *how much* it falls short. A diagnostic run is never a feasible
    design and is never certified as one; it exists to answer the question the bare word
    "infeasible" refuses to.
    """
    n, dt = spec.n, spec.dt
    T = range(n)
    m = pyo.ConcreteModel(name=f"lcet-{spec.project_id}-{spec.operating_year}")
    m.T = pyo.RangeSet(0, n - 1)
    m.M = pyo.RangeSet(0, spec.n_months - 1)
    gens = spec.generation
    gkeys = [g.key for g in gens]
    m.G = pyo.Set(initialize=gkeys, ordered=True)
    gmap = {g.key: g for g in gens}
    bat = spec.battery
    fixed = spec.mode is Mode.MANUAL and spec.fixed_capacities is not None
    fc = spec.fixed_capacities or {}

    # ---- capacity decisions -----------------------------------------------------
    def cap_bounds(_m, k):
        g = gmap[k]
        if fixed:
            v = float(fc.get(g.cap_key, 0.0))
            return (v, v)
        return (g.min_mw, g.max_mw)
    m.cap = pyo.Var(m.G, bounds=cap_bounds, domain=pyo.NonNegativeReals)

    bp_b = ((float(fc.get("bess_power_mw", 0.0)),) * 2 if fixed
            else (bat.min_mw, bat.max_mw if bat.enabled else 0.0))
    be_b = ((float(fc.get("bess_energy_mwh", 0.0)),) * 2 if fixed
            else (bat.min_mwh, bat.max_mwh if bat.enabled else 0.0))
    m.bp = pyo.Var(bounds=bp_b, domain=pyo.NonNegativeReals)
    m.be = pyo.Var(bounds=be_b, domain=pyo.NonNegativeReals)

    # Totals include what is already built. Existing plant is never re-purchased: it
    # enters the physics here and its sunk capex enters no objective term anywhere.
    m.bp_tot = pyo.Expression(expr=m.bp + bat.existing_mw)
    m.be_tot = pyo.Expression(expr=m.be + bat.existing_mwh)

    # ---- dispatch variables -----------------------------------------------------
    m.use = pyo.Var(m.G, m.T, domain=pyo.NonNegativeReals)
    m.curt = pyo.Var(m.G, m.T, domain=pyo.NonNegativeReals)
    m.imp_u = pyo.Var(m.T, bounds=(0, spec.import_limit_mw))
    m.imp_x = pyo.Var(m.T, bounds=(
        0, spec.market_buy_limit_mw if (spec.market_enabled and spec.market_buy_enabled) else 0.0))
    m.exp_u = pyo.Var(m.T, bounds=(0, spec.export_limit_mw if spec.allow_export else 0.0))
    m.exp_x = pyo.Var(m.T, bounds=(
        0, spec.market_sell_limit_mw if (spec.market_enabled and spec.market_sell_enabled) else 0.0))
    m.ch = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.dis = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.soc = pyo.Var(m.T, domain=pyo.NonNegativeReals)
    m.dem = pyo.Var(m.M, domain=pyo.NonNegativeReals)
    # Zero-bounded in a normal run, so the variable exists but cannot relax anything.
    m.unserved = pyo.Var(m.T, bounds=(0, None) if diagnostic else (0, 0))

    # ---- renewable availability -------------------------------------------------
    # Used plus curtailed equals what the commissioned capacity could deliver at the
    # site. Remote sources are converted to delivered energy here, once, so nothing
    # downstream has to remember whether a number is before or after wheeling losses.
    def avail(_m, k, t):
        g = gmap[k]
        return _m.use[k, t] + _m.curt[k, t] == (
            (_m.cap[k] + g.existing_mw) * g.profile[t] * g.delivery_factor)
    m.avail = pyo.Constraint(m.G, m.T, rule=avail)

    # ---- site energy balance ----------------------------------------------------
    aux = bat.aux_frac_of_power
    def balance(_m, t):
        supply = (sum(_m.use[k, t] for k in gkeys) + _m.imp_u[t] + _m.imp_x[t]
                  + _m.dis[t] + _m.unserved[t])
        drain = (spec.load_mw[t] + _m.ch[t] + aux * _m.bp_tot + _m.exp_u[t] + _m.exp_x[t])
        return supply == drain
    m.balance = pyo.Constraint(m.T, rule=balance)

    # ---- shared interconnection -------------------------------------------------
    # Utility, open access and exchange all cross the same physical connection, so the
    # limit binds on their sum, not on each route separately.
    def imp_cap(_m, t):
        return _m.imp_u[t] + _m.imp_x[t] <= spec.import_limit_mw * spec.grid_available[t]
    m.imp_cap = pyo.Constraint(m.T, rule=imp_cap)

    def exp_cap(_m, t):
        return _m.exp_u[t] + _m.exp_x[t] <= spec.export_limit_mw * spec.grid_available[t]
    m.exp_cap = pyo.Constraint(m.T, rule=exp_cap)

    # Wheeled generation also crosses the connection on its way in.
    wheeled = [g.key for g in gens if g.wheeled]
    if wheeled:
        def wheel_cap(_m, t):
            return (sum(_m.use[k, t] for k in wheeled) + _m.imp_u[t] + _m.imp_x[t]
                    <= spec.import_limit_mw * spec.grid_available[t])
        m.wheel_cap = pyo.Constraint(m.T, rule=wheel_cap)

    # ---- battery ----------------------------------------------------------------
    def ch_cap(_m, t):
        return _m.ch[t] <= _m.bp_tot
    def dis_cap(_m, t):
        return _m.dis[t] <= _m.bp_tot
    m.ch_cap = pyo.Constraint(m.T, rule=ch_cap)
    m.dis_cap = pyo.Constraint(m.T, rule=dis_cap)
    m.c_rate = pyo.Constraint(expr=m.bp_tot <= bat.max_c_rate * m.be_tot)

    # SOC never resets at midnight. For a repeating operating year the terminal state is
    # tied back to the first block, so a year cannot start with free stored energy.
    def soc_rec(_m, t):
        prev = _m.soc[n - 1] if t == 0 else _m.soc[t - 1]
        if t == 0 and bat.terminal_rule == "fixed":
            prev = bat.initial_soc_frac * _m.be_tot
        return _m.soc[t] == (bat.retention_per_block * prev
                             + bat.eta_charge * _m.ch[t] * dt
                             - _m.dis[t] * dt / bat.eta_discharge)
    m.soc_rec = pyo.Constraint(m.T, rule=soc_rec)

    def soc_lo(_m, t):
        return _m.soc[t] >= bat.soc_min_frac * _m.be_tot
    def soc_hi(_m, t):
        return _m.soc[t] <= bat.soc_max_frac * _m.be_tot
    m.soc_lo = pyo.Constraint(m.T, rule=soc_lo)
    m.soc_hi = pyo.Constraint(m.T, rule=soc_hi)

    if not bat.allow_grid_charging:
        # Electrons are fungible, so the enforceable form of "charge from renewables
        # only" is that charging in a block cannot exceed renewable output in that block.
        def re_only(_m, t):
            return _m.ch[t] <= sum(_m.use[k, t] for k in gkeys)
        m.re_only = pyo.Constraint(m.T, rule=re_only)

    # ---- monthly billing demand -------------------------------------------------
    # Billed on the measured peak of utility import in each local billing month, floored
    # by any ratchet. Never on an annual average.
    def dem_rule(_m, t):
        return _m.dem[spec.month_index[t]] >= _m.imp_u[t]
    m.dem_peak = pyo.Constraint(m.T, rule=dem_rule)
    def dem_floor(_m, mo):
        return _m.dem[mo] >= spec.min_billable_demand_mw
    m.dem_floor = pyo.Constraint(m.M, rule=dem_floor)

    # ---- optional mode binaries -------------------------------------------------
    blocks = sorted(set(candidate_simultaneity_blocks(spec)) | set(force_binary_blocks or []))
    if blocks:
        m.B = pyo.Set(initialize=blocks, ordered=True)
        m.y_bat = pyo.Var(m.B, domain=pyo.Binary)      # 1 = charging
        m.y_grid = pyo.Var(m.B, domain=pyo.Binary)     # 1 = importing
        # Big-M values are declared constants, not investment variables, so the model
        # stays linear: no product of a capacity variable and a binary appears anywhere.
        Mp = max(bat.max_mw + bat.existing_mw, 1e-6)
        Mg = max(spec.import_limit_mw, spec.export_limit_mw, 1e-6)
        m.excl_ch = pyo.Constraint(m.B, rule=lambda _m, t: _m.ch[t] <= Mp * _m.y_bat[t])
        m.excl_dis = pyo.Constraint(m.B, rule=lambda _m, t: _m.dis[t] <= Mp * (1 - _m.y_bat[t]))
        m.excl_imp = pyo.Constraint(
            m.B, rule=lambda _m, t: _m.imp_u[t] + _m.imp_x[t] <= Mg * _m.y_grid[t])
        m.excl_exp = pyo.Constraint(
            m.B, rule=lambda _m, t: _m.exp_u[t] + _m.exp_x[t] <= Mg * (1 - _m.y_grid[t]))

    # ---- objective ---------------------------------------------------------------
    duty = 1.0 + spec.duty_frac
    capital = sum(m.cap[g.key] * (g.annuity_inr_per_mw_year + g.fixed_om_inr_per_mw_year)
                  for g in gens)
    capital += m.bp * (bat.annuity_inr_per_mw_year + bat.fixed_om_inr_per_mw_year)
    capital += m.be * bat.annuity_inr_per_mwh_year
    capital += spec.existing_fixed_om_inr_year

    util_energy = sum(m.imp_u[t] * dt * spec.energy_inr_per_mwh[t] for t in T) * duty
    util_demand = sum(m.dem[mo] for mo in range(spec.n_months)) \
        * spec.demand_rate_inr_per_mw_month * duty
    mkt_rate = spec.iex_buy_inr_per_mwh + spec.market_txn_inr_per_mwh \
        + (spec.oa_charge_inr_per_mwh if spec.market_oa_applies else 0.0)
    market = sum(m.imp_x[t] * dt * mkt_rate[t] for t in T)
    oa = (sum(m.use[k, t] * dt * spec.oa_charge_inr_per_mwh for k in wheeled for t in T)
          if wheeled else 0.0)
    vom = sum(m.use[g.key, t] * dt * g.variable_om_inr_per_mwh
              for g in gens if g.variable_om_inr_per_mwh for t in T)
    wear = sum(m.dis[t] * dt * bat.wear_inr_per_mwh_discharged for t in T)
    revenue = (sum(m.exp_u[t] * dt * spec.export_inr_per_mwh for t in T)
               + sum(m.exp_x[t] * dt * spec.iex_sell_inr_per_mwh[t] for t in T))

    # The penalty is declared, finite and far above any real price, so a diagnostic run
    # sheds load only where nothing else can serve it.
    voll = float(max(spec.energy_inr_per_mwh.max(),
                     spec.iex_buy_inr_per_mwh.max(), 1.0)) * 1000.0
    shortfall = sum(m.unserved[t] * dt * voll for t in T) if diagnostic else 0.0

    m.obj = pyo.Objective(
        expr=capital + util_energy + util_demand + market + oa + vom + wear - revenue
             + shortfall,
        sense=pyo.minimize)

    m._info = BuildInfo(n_binary_blocks=len(blocks), simultaneity_blocks=blocks, fixed=fixed)
    m._gkeys = gkeys
    return m
