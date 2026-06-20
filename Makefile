# Makefile (repo root) — Autonomous ML Monitoring & Auto-Recovery Agent
#
# Phase 0 operator ergonomics: one venv per service (venva/venvb/venvc/venvd),
# matching docs/deployment_and_devops.md §8 (local dev without Docker). The
# Docker/compose workflow (docs §3, §5) arrives in Phase 5 and will extend this file.
#
# Quick start:  make setup   # build all four venvs + install deps
#               make help    # list every target

PYTHON ?= python3

# Service directories (the venv lives inside each, per docs §8)
MODEL_A_DIR := model-services/model_a
MODEL_B_DIR := model-services/model_b
BACKEND_DIR := control-plane/backend
AGENT_DIR   := control-plane/agent_core

.DEFAULT_GOAL := help
.PHONY: help setup setup-model-a setup-model-b setup-backend setup-agent \
        env data generate-data train-models sample-input live-batch reference-summary \
        run-model-a run-model-b backend-init run-backend agent demo verify-config \
        test test-unit test-int test-e2e clean

# data_sim scripts run under venva (has sklearn/pandas/numpy/joblib) and import
# their sibling `common` module, so they execute from the data_sim/ directory.
VENVA_PY := $(abspath $(MODEL_A_DIR)/venva/bin/python)

## Show this help (default)
help:
	@echo "Autonomous ML Monitoring & Auto-Recovery Agent — make targets:"
	@echo ""
	@grep -E '^## ' -A1 $(MAKEFILE_LIST) | \
	  awk '/^## / {desc=substr($$0,4)} /^[a-zA-Z0-9_-]+:/ {printf "  \033[36m%-16s\033[0m %s\n", substr($$1,1,length($$1)-1), desc}'
	@echo ""

# ---- Setup: one isolated venv per service -------------------------------

## Build all four venvs (venva/venvb/venvc/venvd) and install their deps
setup: setup-model-a setup-model-b setup-backend setup-agent
	@echo "==> All venvs ready. Next: implement Phase 1 (model services)."

setup-model-a:
	@echo "==> venva: $(MODEL_A_DIR)"
	$(PYTHON) -m venv $(MODEL_A_DIR)/venva
	$(MODEL_A_DIR)/venva/bin/pip install --upgrade pip
	$(MODEL_A_DIR)/venva/bin/pip install -r $(MODEL_A_DIR)/requirements.txt

setup-model-b:
	@echo "==> venvb: $(MODEL_B_DIR)"
	$(PYTHON) -m venv $(MODEL_B_DIR)/venvb
	$(MODEL_B_DIR)/venvb/bin/pip install --upgrade pip
	$(MODEL_B_DIR)/venvb/bin/pip install -r $(MODEL_B_DIR)/requirements.txt

setup-backend:
	@echo "==> venvc: $(BACKEND_DIR)"
	$(PYTHON) -m venv $(BACKEND_DIR)/venvc
	$(BACKEND_DIR)/venvc/bin/pip install --upgrade pip
	$(BACKEND_DIR)/venvc/bin/pip install -r $(BACKEND_DIR)/_files/requirements.txt

setup-agent:
	@echo "==> venvd: $(AGENT_DIR)"
	$(PYTHON) -m venv $(AGENT_DIR)/venvd
	$(AGENT_DIR)/venvd/bin/pip install --upgrade pip
	$(AGENT_DIR)/venvd/bin/pip install -r $(AGENT_DIR)/_files/requirements.txt

## Create a local .env from .env.example (does not overwrite an existing .env)
env:
	@if [ -f .env ]; then \
	  echo ".env already exists — leaving it untouched."; \
	else \
	  cp .env.example .env && \
	  echo "Created .env from .env.example — fill in secrets before running."; \
	fi

# ---- Data & models (Phase 1; needs venva — run `make setup-model-a`) ----

## Generate reference data, train models, sample inputs, and drift reference
data: generate-data train-models sample-input live-batch reference-summary
	@echo "==> Data + models ready (model.pkl, sample_input.csv, reference_window.json)."

## Generate the frozen 20k reference dataset (data_sim/artifacts/reference.csv)
generate-data:
	cd data_sim && $(VENVA_PY) generate_reference.py

## Train model_a (GradientBoosting) + model_b (LogisticRegression) -> model.pkl
train-models:
	cd data_sim && $(VENVA_PY) train_models.py

## Refresh committed sample_input.csv (+ drift variant) in both services
sample-input:
	cd data_sim && $(VENVA_PY) make_sample_input.py

## Generate committed live-batch fixtures (data_sim/fixtures/*_batch.csv)
live-batch:
	cd data_sim && $(VENVA_PY) make_live_batch.py

## Build the committed drift reference window (detection/reference_window.json)
reference-summary:
	cd data_sim && $(VENVA_PY) build_reference_summary.py

# ---- Run components (wired now; serve once Phase 1+ fills the modules) ---

## Run model_a (ACTIVE) on :8001  [needs Phase 1]
run-model-a:
	cd $(MODEL_A_DIR) && venva/bin/uvicorn app:app --host 0.0.0.0 --port 8001 --reload

## Run model_b (BACKUP) on :8002  [needs Phase 1]
run-model-b:
	cd $(MODEL_B_DIR) && venvb/bin/uvicorn app:app --host 0.0.0.0 --port 8002 --reload

## Initialize the backend DB (migrate + seed registry)
backend-init:
	cd $(BACKEND_DIR) && venvc/bin/python manage.py migrate --noinput
	cd $(BACKEND_DIR) && venvc/bin/python manage.py seed_demo

## Run the Django control plane on :8000 (local dev: DEBUG on for friendly errors)
run-backend:
	cd $(BACKEND_DIR) && DJANGO_DEBUG=1 venvc/bin/python manage.py runserver 0.0.0.0:8000

## Run the agent loop in the foreground (e.g. make agent ARGS="--ticks 6")
agent:
	cd $(AGENT_DIR) && venvd/bin/python _files/agent.py $(ARGS)

## MVP demo: faulty model_a -> agent detects, switches to model_b, verifies
demo:
	bash scripts/demo_mvp.sh

## Smoke-check that schemas.py + config.py import cleanly under venvd
verify-config:
	cd $(AGENT_DIR)/_files && ../venvd/bin/python -c "import schemas, config; print('schemas + config import OK')"

# ---- Tests (roadmap §6) --------------------------------------------------

## Run the full test matrix (agent unit + e2e scenarios + backend)
test: test-unit test-e2e

## Unit tests: agent detectors/decision/verify + Django backend apps
test-unit:
	cd $(AGENT_DIR) && venvd/bin/python -m pytest tests/test_detectors.py \
	  tests/test_decision_engine.py tests/test_verification.py -q
	cd $(BACKEND_DIR) && venvc/bin/python manage.py test -v1

## E2E scenario tests (failure_scenarios.md cases through the loop)
test-e2e:
	cd $(AGENT_DIR) && venvd/bin/python -m pytest tests/test_loop_scenarios.py -q

## Integration (agent <-> live stack) is exercised by `make demo`
test-int:
	@echo "test-int: run 'make demo' for the live agent<->services<->backend loop."

# ---- Cleanup ------------------------------------------------------------

## Remove all venvs and Python caches
clean:
	rm -rf $(MODEL_A_DIR)/venva $(MODEL_B_DIR)/venvb $(BACKEND_DIR)/venvc $(AGENT_DIR)/venvd
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	@echo "==> Cleaned venvs and __pycache__."
