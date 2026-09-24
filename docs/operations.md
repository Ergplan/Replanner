# Running it

Python 3.12 is required — 3.14 has no wheels for the solver stack, and its `ensurepip`
is broken on this machine.

```bash
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

## Solve from the command line, with no API and no browser

```bash
./.venv/bin/python datasets/synthetic/generator.py            # writes the seeded year
PYTHONPATH=packages/energy_core ./.venv/bin/python -m energy_core.cli optimise \
    --year 2026 --out runs/modeA
PYTHONPATH=packages/energy_core ./.venv/bin/python -m energy_core.cli certify \
    --run runs/modeA --year 2026
```

`certify` needs nothing from the solver except the numbers it wrote down, which is the
point of it.

## The service

```bash
PYTHONPATH=packages/energy_core ./.venv/bin/python services/worker/worker.py &
PYTHONPATH=packages/energy_core ./.venv/bin/python -m uvicorn main:app \
    --app-dir services/api --port 8099 &
npm --prefix apps/web run dev            # http://localhost:3210
```

Or `make worker`, `make api` and `make web`, each in its own terminal.

The worker is a separate process on purpose: solves do not run in request handlers, and
job state is on disk so a restart of either process loses nothing. Each solve runs in a
child process, so Cancel, or a newer manual scenario replacing an older one, stops the
solver within a second instead of letting it finish unwanted work.

## Using the app

1. **Setup** (`/setup`). The sample project is read-only. Name a new project, and it
   starts as a copy of the sample. Edit the site, existing plant, tariff, open access,
   technologies and finance, and upload a year of load as CSV: a timestamp column and a
   load column, 15-, 30- or 60-minute, in kW, MW or kWh per interval. Solar and wind
   output, exchange prices and grid availability upload the same way. A file with gaps
   or duplicates is refused with the exact timestamps; nothing is filled in silently.
2. **Save and find optimum** saves the inputs and opens the twin, which starts the
   solve. It takes a few minutes; the page shows elapsed time and fills in when the
   result is certified.
3. **Optimise & explore** (`/`). Play through the year, and use Manual scenario to fix
   capacities and see the premium over the optimum.

Projects live in `runs/_projects/<id>/`: `inputs.json` and any uploaded series. Load,
solar and wind output profiles, exchange prices and grid availability can each be
uploaded; whatever is not comes from the synthetic sample. A user project
assumes the grid is always available, because the sample's six-hour outage is a solver
test case, not a fact about your site.

## Tests

```bash
./.venv/bin/python -m pytest tests -q
```

## Measured on this machine

Apple Silicon, Python 3.12.5, HiGHS 1.11.0, one operating year of 35,040 blocks:

| | Mode A | Mode B |
|---|---|---|
| Variables | 490,577 | 490,577 |
| Constraints | 455,534 | 455,534 |
| Model build | 0.6 s | 0.6 s |
| Solve | 197 s | 26 s |
| Certification | ~2 s | ~2 s |
| Gap | 3.7e-15 | 3.9e-15 |

The variable count includes one diagnostic unserved-load slack per block, bounded to
zero in every normal run. The timings and gaps in the table were measured before the
rooftop limit and the battery warranty constraint were added; the counts are current.

On a 4-core Linux container (same versions), Mode A took 385 s and Mode B 69 s with the
rooftop limit. Adding the warranty constraint took Mode A to 637 s for the same optimum,
to within one ulp. That constraint is a single dense row across every discharge variable
in the year, and HiGHS pays for it even though it is slack on the seeded project.

With banking on, Mode A is much slower. The run used illustrative rules: monthly
settlement, an 8% in-kind charge, no drawal 18:00–22:00 local, and deposits capped at 30%
of monthly load. On the same container it took 2,841 s for 595,697 variables and 525,626
constraints. It certified at a gap of 1.2e-13, 35,040 / 35,040 blocks checked. The cost
fell from INR 778.2 M to 740.5 M a year: open-access solar roughly doubled, open-access
wind all but disappeared, and 19,906 MWh was drawn from 21,637 MWh banked, with nothing
lapsing. These are illustrative rules, not any state's order.

Mode B is much faster because the capacity columns are pinned, which is what makes the
slider workflow usable. It is still far too slow for a keystroke, which is why the UI
debounces, supersedes obsolete jobs and shows the previous result with a stale marker
rather than inventing a progress bar the solver does not report.
