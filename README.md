# Least-cost energy planning and factory digital twin

Industrial electricity planning for an Indian site: what to build, how to dispatch it
across a full year of 15-minute blocks, and what a different choice would cost. Built for
**jouleWise**, with **ergOS** as the dispatch orchestration layer.

**This is a planning and simulation digital twin.** Playback steps through solved records.
It is not live plant telemetry and it does not control equipment.

## What it does

**Mode A — Find optimum.** Jointly optimises new capacity (onsite solar, open-access solar
and wind, storage MW and MWh) and chronological dispatch to minimise equivalent annual
cost, over all 35,040 blocks of the operating year.

**Mode B — Manual scenario.** Fixes the capacities *on the backend* and re-optimises
dispatch only. Capital and fixed O&M for what you chose stay in the total, and the premium
against the certified optimum is reported in rupees and per cent.

Both modes use the same model builder, cost engine, constraints, tariff engine and
validator. That is what makes the two numbers comparable.

## The part that matters

No design is accepted without **independent full-resolution certification**. A separate
validator recomputes the physics and the money from the immutable inputs and the exported
decisions, block by block, and reconciles its own total against the solver objective. It
does not read the solver's constraint residuals — those only prove the solver satisfied
the constraints it was given, which cannot catch a constraint written wrongly, a unit
converted twice, or a cost never added to the objective.

The coverage claim is rendered only from counts the validator actually walked, and a run
that fails does not get a badge.

On the seeded project:

```
Mode A   INR 768,523,537.81/yr   gap 3.7e-15   certified   35,040 / 35,040 blocks checked
Mode B   INR 768,523,537.81/yr   at the same capacities — delta exactly 0
```

## Layout

```
packages/energy_core/     schemas, ingestion, tariffs, finance, optimisation,
                          degradation, validation, reporting, CLI
services/api/             FastAPI: accepts work, reads artefacts, never solves
services/worker/          durable file-backed jobs and the solve loop
apps/web/                 Next.js + TypeScript twin, isometric scene, playback
datasets/synthetic/       seeded generator, adversarial fixtures, project definition
tests/                    analytical, physical and validator-mutation tests
docs/                     equations mapped to code, assumptions, operations
```

## The twin

The isometric scene reuses the jouleWise 2:1 projection from the existing drawing system
(`apps/web/lib/iso.ts`). Every shape is generated from plan coordinates, so the panel
array grows, containers appear and flow arrows reverse as the solved numbers change,
without the picture drifting out of step with them. Flow widths and speeds read one solved
15-minute record; paused, the arrows show exactly what the optimiser decided for the block
on screen. Near-zero flows are hidden rather than drawn faint.

## Honest limits

- One operating year. Multi-year staging and degradation are schema'd and documented as
  **unsupported**, and fail validation rather than being quietly approximated.
- Perfect foresight. A planning bound, not a claim about a real-time controller.
- Everything shipped is illustrative — not metered load, not vendor quotations, not a
  statement of any statutory charge.

`docs/assumptions.md` lists what is supported, what is not, and the judgement calls.
`docs/operations.md` has setup, commands and measured performance.

## Quick start

```bash
./.venv/bin/python datasets/synthetic/generator.py
PYTHONPATH=packages/energy_core ./.venv/bin/python -m energy_core.cli optimise --out runs/modeA
PYTHONPATH=packages/energy_core ./.venv/bin/python -m energy_core.cli certify --run runs/modeA
./.venv/bin/python -m pytest tests -q
```
