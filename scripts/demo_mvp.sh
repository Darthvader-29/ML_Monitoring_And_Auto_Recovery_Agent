#!/usr/bin/env bash
# Phase 2 MVP demo — the full Observe->Detect->Decide->Act->Verify loop.
#
# Boots model_a with an injected error fault and a clean model_b, then runs the
# agent for a few ticks. Expected: the agent detects the HIGH error rate, waits
# CONFIRM_N cycles (anti-flap), switches A->B, and verifies model_b is healthy.
#
# Reproduces failure_scenarios.md A1 (error-rate spike) + N1 (no flap on the first
# transient HIGH). Requires `make setup` and `make data` to have been run.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

cleanup() { pkill -f "uvicorn app:app" 2>/dev/null; }
trap cleanup EXIT

echo "==> starting model_a (FAULT_ERROR_RATE=0.6) and model_b (clean)"
( cd model-services/model_a && FAULT_ERROR_RATE=0.6 \
    nohup venva/bin/uvicorn app:app --host 127.0.0.1 --port 8001 >/tmp/demo_ma.log 2>&1 & )
( cd model-services/model_b && \
    nohup venvb/bin/uvicorn app:app --host 127.0.0.1 --port 8002 >/tmp/demo_mb.log 2>&1 & )

echo "==> waiting for services"
for _ in $(seq 1 30); do
  curl -sf http://127.0.0.1:8001/health >/dev/null 2>&1 \
    && curl -sf http://127.0.0.1:8002/health >/dev/null 2>&1 && break
  sleep 1
done

echo "==> running agent (5 ticks)"
( cd control-plane/agent_core && venvd/bin/python _files/agent.py --ticks 5 --interval 1 )
