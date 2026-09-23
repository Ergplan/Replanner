PY := ./.venv/bin/python
export PYTHONPATH := packages/energy_core

.PHONY: data optimise certify test api worker web all
data:     ; $(PY) datasets/synthetic/generator.py
optimise: ; $(PY) -m energy_core.cli optimise --year 2026 --out runs/modeA
certify:  ; $(PY) -m energy_core.cli certify --run runs/modeA --year 2026
test:     ; $(PY) -m pytest tests -q
worker:   ; $(PY) services/worker/worker.py
api:      ; $(PY) -m uvicorn main:app --app-dir services/api --port 8099
web:      ; npm --prefix apps/web run dev
all: data optimise certify test
