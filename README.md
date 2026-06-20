# ML Monitoring & Auto-Recovery Agent

An autonomous, closed-loop system that continuously monitors deployed ML models,
detects degradation (system faults, anomalies, data/concept drift), decides on a
recovery, executes it, and verifies the result — **Observe → Detect → Decide →
Act → Verify** — without human intervention. It is a rule-based + statistical
*agent*, not an LLM and not a passive dashboard.

The authoritative design lives in [`docs/`](./docs) (start with
[`docs/architecture.md`](./docs/architecture.md) and
[`docs/implementation_roadmap.md`](./docs/implementation_roadmap.md)).

## Status

Phases 0–7 of the roadmap are implemented and tested (the full Definition of Done).
Phase 8 (bonus/stretch) is optional and in progress — the first item is built behind
a flag; the rest are not yet started.

| Phase | What | State |
|-------|------|-------|
| 0 | Foundations — deps, shared `schemas.py`/`config.py`, Makefile, `.env` | ✅ |
| 1 | Two FastAPI model services + trained `model.pkl` + simulated data | ✅ |
| 2 | MVP closed loop (threshold detection, direct switch executor) | ✅ |
| 3 | Detection depth — anomaly (robust-z/IQR/EWMA) + drift (PSI/KS/chi²) | ✅ |
| 4 | Django + DRF control plane: registry, metrics, audit (survives restart) | ✅ |
| 5 | Jenkins recovery executor + Docker packaging (direct stays the default) | ✅ |
| 6 | Read-only operator dashboard (health / drift / action timeline) | ✅ |
| 7 | Test matrix (34 tests) + client retry/backoff + anti-flap | ✅ |
| 8 | Bonus — confidence-based action thresholds (`CONFIDENCE_GATING_ENABLED`, off by default) | 🔶 1/5 |

## Architecture

Independent services, **HTTP everywhere** (no shared imports/DB):

| Service        | Path                       | Port | venv  |
| -------------- | -------------------------- | ---- | ----- |
| Model A (active) | `model-services/model_a` | 8001 | `venva` |
| Model B (backup) | `model-services/model_b` | 8002 | `venvb` |
| Django backend | `control-plane/backend`    | 8000 | `venvc` |
| Agent core     | `control-plane/agent_core` | —    | `venvd` |
| Jenkins        | `devops/jenkins`           | 8080 | —     |

The agent has **no inbound port** — it is a pure outbound HTTP client.

## Quickstart (local, no Docker)

```bash
make setup          # create the four venvs + install deps
make data           # generate reference data, train model_a/model_b, build fixtures
make backend-init   # migrate the DB + seed the registry (model_a active, model_b backup)

# In separate terminals (or backgrounded):
make run-backend    # Django on :8000  (dashboard at http://localhost:8000/dashboard/)
make run-model-a    # model_a on :8001
make run-model-b    # model_b on :8002
make agent ARGS="--ticks 10 --interval 2"   # run the loop

make test           # full matrix: agent unit + e2e scenarios + backend (30 tests)
```

## Demo — autonomous A→B recovery

```bash
make demo
```

Boots a **faulty** model_a (injected error rate) and a clean model_b, then runs the
agent: it detects the HIGH error rate, waits `CONFIRM_N` cycles (anti-flap),
switches traffic A→B, and verifies model_b is healthy — visible live on the
dashboard and in `/api/actions`. Inject drift instead with
`make agent ARGS="--inject-drift --ticks 6"`.

## Docker (Phase 5)

```bash
docker compose -f devops/docker/docker-compose.yml up -d   # 5 services on mlmon_net
```

Set `AGENT_EXECUTOR_TYPE=jenkins` to route recovery through Jenkins jobs; the
default `direct` executor keeps the system runnable without Jenkins.

