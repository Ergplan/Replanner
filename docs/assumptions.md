# Assumptions, and what is not supported

## Everything shipped is illustrative

The seeded dataset and the seeded project are examples. They are **not** metered load,
**not** vendor quotations, and **not** a statement of any statutory charge. The
open-access stack in particular — transmission, wheeling, cross-subsidy surcharge,
additional surcharge, scheduling — is written as configurable, effective-dated inputs
precisely because those are set by state commission orders and change. Replace them with
the order in force before quoting anything.

## Supported

- One operating year at full 15-minute chronology, 35,040 blocks (35,136 in a leap year).
- Joint capacity and dispatch optimisation (Mode A); fixed-capacity dispatch (Mode B).
- Onsite and open-access generation with separate delivery losses and charge stacks.
- Retail time-of-day energy rates, effective-dated; monthly demand charges on a measured
  peak with an optional ratchet; kVA billing at a declared power factor; electricity duty.
- Exchange purchase against an uploaded forecast series, including negative prices.
- Discrete equipment sizes for generation (`AssetOption.step_mw`): new capacity is a whole
  number of units, which makes Mode A a MILP. Mode B pins capacity to a size already
  checked to be on the grid, so it stays an LP. Battery power and energy are continuous.
- Storage with one-way efficiencies, SOC bounds, C-rate, auxiliaries, self-discharge, a
  cyclic terminal rule, and throughput-priced wear.
- Independent full-resolution certification, and a diagnostic relaxation that locates
  shortfalls in an infeasible scenario.

## Not supported in this release

These **fail validation rather than being silently ignored**. The check is
`optimization/supported.py`; `build_spec` runs it before any model is built, the API runs
it again at submission so a bad scenario is refused immediately rather than failing as a
job, and every problem is reported at once.

- Multi-year horizons with staged expansion and year-on-year degradation. The schema
  carries `ExpansionPhase` and `operating_years`, and the finance module has the
  discounted-cash-flow machinery, but the model builder solves one operating year. A
  one-year result is labelled as such and does not satisfy multi-year acceptance.
- Banking and settlement of wheeled energy. `OpenAccessCharges.banking_enabled` exists;
  enabling it without an intertemporal balance, expiry and settlement rules would be
  unlimited free storage, so it is rejected.
- Monetising degradation through modelled replacement or augmentation cash flows. Only
  the throughput-wear approximation is implemented.
- A `step_mw` for which no whole number of units lies between the option's `min_mw` and
  `max_mw`.
- Existing plant that is commissioned after the operating year starts, or retires within
  or before it. A retirement date after the year is accepted, since it changes nothing.
- Two asset options of the same technology. Capacities are reported per technology, so
  the second would overwrite the first and existing plant would be counted twice.
- Existing plant whose technology has no asset option. The option supplies the generation
  profile; without one the plant's energy would be dropped while its O&M was still charged.
  An option that is present but disabled still supplies the profile — it just cannot buy
  more.
- Mode B capacities under a key that is not one of the five capacity names, negative,
  non-zero for a technology no enabled option can build, outside the option's
  `[min_mw, max_mw]`, or off its `step_mw` grid. A manual scenario must be one Mode A was
  allowed to choose; otherwise it can undercut the certified optimum and the premium
  stops meaning anything. A key left out is fixed at zero, so it is checked at zero.

## Judgement calls worth knowing about

**Simultaneity binaries are added selectively.** Carrying 70,000 binaries for a handful
of negative-price intervals would make a full year impractical. Binaries go only where
the price arithmetic could reward a physically impossible flow; the independent validator
then checks every block, and anything it finds is fed back and re-solved. The correctness
of the answer rests on the check, not on the heuristic that made it fast.

**Renewable-only charging is enforced per block.** Electrons are fungible, so
"charge from renewables only" is enforced as `charge_t ≤ Σ renewable_t`. Attribution
beyond that is an accounting convention, not a physical fact.

**Perfect foresight.** Dispatch is optimised against a known year. That is a planning
bound, not evidence of what a real-time controller would achieve.

**PostgreSQL is specified but not used locally.** Run and job state is file-backed
(`runs/`, atomic writes) because no Postgres is available in this environment. The store
is behind `services/worker/jobs.py`; swapping it is a contained change.
