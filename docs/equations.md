# Equations, and where each one lives in the code

Units are MW, MWh and INR throughout the core. Conversions happen at the boundaries — the
tariff engine turns INR/kWh into INR/MWh once, the frontend formats for display — and
nowhere else. Every interval is Δt = 0.25 h.

| Symbol | Meaning | Unit |
|---|---|---|
| `t` | interval index, `0 … n−1` | — |
| `Δt` | interval duration, always 0.25 | h |
| `L_t` | site load | MW |
| `cf_{g,t}` | capacity factor of option `g` | — |
| `η_g` | delivery factor, `1 − loss` (1 for onsite) | — |
| `C_g` | new capacity of option `g` | MW |
| `E_g` | existing capacity of option `g` | MW |
| `u_{g,t}`, `x_{g,t}` | used / curtailed generation | MW |
| `i^u_t`, `i^x_t` | utility and exchange import | MW |
| `e^u_t`, `e^x_t` | export to utility and exchange | MW |
| `c_t`, `d_t` | battery charge / discharge, AC | MW |
| `S_t` | state of charge at end of `t` | MWh |
| `P`, `Q` | battery power and energy bought | MW, MWh |
| `D_m` | billing demand in month `m` | MW |

## Objective — `optimization/model.py`

Minimise equivalent annual cost:

```
min  Σ_g C_g (a_g + f_g)  +  P (a_P + f_P)  +  Q a_Q  +  F_existing
   + (1 + δ) [ Σ_t i^u_t Δt π_t  +  Σ_m D_m ρ ]
   + Σ_t i^x_t Δt (π^x_t + τ + ω·1_oa)
   + Σ_g,wheeled Σ_t u_{g,t} Δt ω
   + Σ_g Σ_t u_{g,t} Δt v_g
   + Σ_t d_t Δt w
   − Σ_t (e^u_t Δt π^e + e^x_t Δt π^s_t)
```

`a` is an annuity, `f` fixed O&M, `δ` electricity duty, `π_t` the retail energy rate,
`ρ` the demand rate, `ω` the open-access charge stack, `τ` the exchange transaction
charge, `v` variable O&M, `w` battery wear.

**Annuities — `finance/annualise.py`.** `CRF(r,n) = r(1+r)^n / ((1+r)^n − 1)`, and
`CRF(0,n) = 1/n`. A capital item is charged once, as an annuity *or* as a discounted cash
flow, never both. Existing assets contribute `F_existing` (their fixed O&M) and their
physics; their sunk capital appears in no term.

## Physics — `optimization/model.py`

**Availability.** `u_{g,t} + x_{g,t} = (C_g + E_g) · cf_{g,t} · η_g`. Remote sources are
converted to delivered energy here, once, so nothing downstream must remember whether a
number is before or after wheeling losses.

**Site balance.** `Σ_g u_{g,t} + i^u_t + i^x_t + d_t + s_t = L_t + c_t + α P_tot + e^u_t + e^x_t`
where `α` is the auxiliary fraction of rated power and `s_t` is the diagnostic slack,
bounded to zero in any run that is not an explicit diagnostic.

**Storage.** `S_t = retention · S_{t−1} + η_c c_t Δt − d_t Δt / η_d`, with
`retention = (1 − self-discharge/day)^{Δt/24}`, and `S_{−1} ≡ S_{n−1}` for the repeating
year. SOC is never reset at midnight. Bounds are
`soc_min · Q_tot ≤ S_t ≤ soc_max · Q_tot`, so depth of discharge is applied once, to
energy. Power is bounded by `P_tot`, and `P_tot ≤ C-rate · Q_tot`.

**Connection.** `i^u_t + i^x_t ≤ Ī · g_t` and, because wheeled generation crosses the same
wires, `Σ_wheeled u_{g,t} + i^u_t + i^x_t ≤ Ī · g_t`.

**Billing demand.** `D_m ≥ i^u_t` for every `t` in month `m`, and `D_m ≥ ratchet · contract`.
Months are local calendar months — which is why the canonical index spans a local year
(`ingestion/chronology.py`).

**Site area.** For onsite solar, `C_g ≤ roof + land − E_g`, applied as the upper bound of
`C_g` together with the option's own `max_mw`. The roof holds a fixed MWp, and panels
already on it take part of that.

**Discrete sizes.** For an option with a unit size `s_g`, `C_g = s_g · k_g` with `k_g`
a non-negative integer in `[⌈min/s_g⌉, ⌊max/s_g⌋]`. Only Mode A carries `k_g`; Mode B
fixes `C_g` directly, on a value the input check has already put on the grid. A MIP solve
stops at `mip_gap`, so a stepped Mode A result is `certified` only when the reported gap
is at most 1e-6. Otherwise it is `certified_incumbent`: feasible and fully checked, but
not proven optimal.

**Prohibited simultaneity.** Binaries with declared constant big-M values, added only on
blocks where prices could reward the behaviour, then checked on every block by the
validator and fed back if anything is found. No product of an investment variable and a
binary appears anywhere.

## Degradation — `optimization/spec.py`

Wear is priced against throughput: `w = capex_MWh / (EFC · usable_fraction) + vom`. The
cell annuity recovers the calendar-life replacement; charging both for the same liability
would be a double count, so wear is a throughput signal only, and the warranty envelope
is checked separately. Installed energy is kept apart from remaining usable energy, and
no bilinear `capacity × endogenous SOH` product is formed.

## Certification — `validation/certify.py`

The validator recomputes all of the above from the immutable `RunSpec` and the exported
decisions, block by block, and reconciles its own total against the solver objective.
Capacities are checked too: each one within its option's range and, where the option has
a unit size, within 1e-6 MW of a whole number of units. It
never reads the solver's constraint residuals: those only prove the solver satisfied the
constraints it was given, which cannot catch a constraint written incorrectly, a unit
converted twice, or a cost never added to the objective.
