# Code Audit & Improvement Plan

_Deep analysis of the Autonomous ML Monitoring & Auto-Recovery Agent._

This document records (1) the bugs that were found and **fixed**, and (2–5) the
structural improvements — decoupling, de-duplication, data-abstraction/privacy —
that were **designed but deferred** so they can be executed as a focused second
wave without entangling them with the bug fixes.

Baselines after the fix wave: **agent_core 29 tests, backend 21 tests, model
services compile-clean and byte-identical, no missing migrations.**

---

## 1. Bugs fixed (one git worktree / branch per issue)

Each bug was fixed in an isolated worktree, verified (existing suite + a new
regression test), and merged `--no-ff` into the integration branch. Branch names
in parentheses.

### Agent core
| # | Bug | File | Fix |
|---|-----|------|-----|
| 1 | A fully **collapsed `0.0` confidence passed verification** as "recovered" (`not post_conf` is falsy-but-not-None) | `verification/health_check.py` | `post_conf is None or post_conf >= floor` (`bugfix/agent-healthcheck-confidence`) |
| 2 | Anomaly `DetectionResult.metric` was **hardcoded `"robust_zscore_spike"`** for every metric/method — erased which signal fired and lied about spike-vs-sustained | `detection/anomaly_detector.py` | carry `f"{metric}_{pattern}"` (`bugfix/agent-anomaly-metric`) |
| 3 | `_worst()` could pick a **non-breaching** detection (e.g. the clean drift aggregate) as the Decision's evidence | `decision_engine/decision.py` | filter to `anomaly_detected` first (`bugfix/agent-decision-worst`) |
| 4 | `datetime.utcnow()` (deprecated, **naive**) → ambiguous at the tz-aware Django boundary | `_files/schemas.py` | tz-aware `_utcnow()` (`bugfix/agent-schemas-utcnow`) |
| 5 | `config.feature_names` was a **stale 6-name placeholder** (`feature_1..6`) matching nothing | `_files/config.py` | the real 8-feature schema (`bugfix/agent-config-features`) |

### Model services
| # | Bug | Fix |
|---|-----|-----|
| 6 | `/predict` assumed **integer, binary, index-1** labels (`int(classes[i])`, `proba[1]`) — crashes on string/non-int labels | derive prediction/score from `classes_` via a testable `_derive_prediction` helper (`bugfix/model-predict-labels`) |
| 7 | **p95 latency reported `0.0`** when every request in the window errored — a slow/failing service looked fast | fall back to error-sample latencies when no successes (`bugfix/model-metrics-empty-latency`) |

### Backend (Django)
| # | Bug | Fix |
|---|-----|-----|
| 8 | `executed_at` **overwritten on every PATCH** (incl. verification-only) — corrupted the append-only audit timeline | stamp once, only on first outcome (`bugfix/actions-executed-at`) |
| 9 | A **REVERT verdict closed the incident as ESCALATED** — collapsed the 3-way KEEP/REVERT/ESCALATE state machine | REVERT keeps the incident `RECOVERING`/open (`bugfix/actions-revert-incident`) |
| 10 | `is_reversible` was a **tautology (always True)** and semantically backwards | meaningful `_REVERSIBLE = {SWITCH, ROLLBACK, DISABLE}` (`bugfix/actions-is-reversible`) |
| 11 | `_OUTCOME_MAP` mapped **`skipped` → `SUCCESS`** | added a real `SKIPPED` outcome + migration (`bugfix/actions-outcome-skipped`) |
| 12 | Unvalidated `?limit` → **500 on non-int / crash on negative / unbounded fetch** | `_parse_limit` clamps to `[1, 500]` (`bugfix/limit-param-validation`) |
| 13 | Untrusted/naive client **`timestamp` stored unparsed** — a bad string could 500 the ingest or sort stale rows as "latest" | `_parse_timestamp` parses + tz-normalizes (`bugfix/monitoring-timestamp`) |
| 14 | Ingestion **auto-created phantom registry rows** from typo'd names (`"model_a "`) | normalize/strip names at the ingestion chokepoint (`bugfix/phantom-registry-names`) |
| 15 | The **"one globally-active version" invariant was unenforced** at the DB layer (constraint was per-model) | global partial-unique constraint + migration (`bugfix/registry-one-active`) |
| 16 | `POST /active-model` **ignored the documented `reason`** and would promote `DEPRECATED`/`ROLLED_BACK` versions | audit the reason; skip retired statuses on auto-select (`bugfix/registry-active-model-gate`) |
| 17 | Read APIs **exposed internal `endpoint_url`/`port` and raw metric/detection blobs** to any reader | gate behind `EXPOSE_INTERNAL_TOPOLOGY` (default on for the demo) (`bugfix/serializer-data-privacy`) |

---

## 2. Decoupling — template / strategy / dependency-injection (DESIGN)

The three places the codebase is hardest to extend share **one root cause:
non-uniform interfaces** that force `if/elif` chains. Give each family a single
interface (an abstract base / `Protocol`), then select implementations through a
factory — exactly the "write the contract as a class and hand back an object so
implementations are swappable" pattern requested.

### 2.1 Executor strategy (recovery backend)
Today `actions/switch_model.py` branches inline: `if settings.executor_type ==
"jenkins": ...` and lazily imports `JenkinsClient`. Adding a third backend means
editing the action.

```python
# actions/executors.py
class Executor(Protocol):
    def switch(self, target: str, reason: str) -> ExecutorResult: ...

class DirectExecutor:   # in-memory flip (+ Django mirror)
    ...
class JenkinsExecutor:  # runs the recovery job
    ...

def make_executor(settings) -> Executor:        # the ONE place that knows concretes
    return {"direct": DirectExecutor,
            "jenkins": JenkinsExecutor}[settings.executor_type]()
```
`switch_model.execute` calls `executor.switch(...)` with no branch.

### 2.2 Detector registry
`detection/` already hints at a uniform interface (the vestigial module-level
`detect()` stubs) but never landed it — the three detectors expose
`detect(**kwargs)`, `.evaluate(dict)`, `.evaluate_data_drift(rows)`. Unify:

```python
class Detector(Protocol):
    name: str
    def evaluate(self, ctx: TickContext) -> list[DetectionResult]: ...

DETECTORS = [ThresholdDetector(), AnomalyDetector(), DriftDetector()]
detections = [d for det in DETECTORS for d in det.evaluate(ctx)]
```
Adding a detector becomes registration, not editing `run_tick`.

### 2.3 Action-handler registry
`agent.py:run_tick` dispatches actions with an `if/elif` over `ActionType` and the
handlers have mismatched signatures (`switch_model.execute(decision, runtime, dj)`
vs `alert.execute(decision)`). Give every handler the same `(ctx)` signature and a
table:

```python
ACTION_HANDLERS: dict[ActionType, Handler] = {
    ActionType.SWITCH_BACKUP: switch_model.execute,
    ActionType.DISABLE_PREDICTIONS: switch_model.execute,
    ActionType.ALERT: alert.execute,
    ActionType.NO_OP: no_op.execute,
}
result = ACTION_HANDLERS.get(decision.action, no_op.execute)(ctx)
```
This mirrors Django's `_ACTION_MAP` table on the other side of the wire.

### 2.4 Inject the Django client
`django_client.get_client()` welds a reachability probe to concrete-class
selection. Declare a `DjangoClientProtocol` (already satisfied implicitly by
`DjangoClient`/`NullDjangoClient`), inject it into `run()`, and keep `get_client()`
as an overridable factory so tests can hand in a fake without monkeypatching.

### 2.5 Backend service layer (Incident state machine)
The Incident lifecycle (`OPEN→RECOVERING→RESOLVED/ESCALATED`) is implemented
ad-hoc inside `actions_app/views.py`. Move it onto the model / a service object —
`Incident.recover()`, `Incident.escalate()`, `Incident.keep_open()` — so the
transition rules live in one place (the same place bug #9 had to be fixed).

---

## 3. De-duplication — single source of truth (DESIGN)

There is currently **no shared util module** in `agent_core`; every helper is
re-declared. Recommended homes: a new `control-plane/agent_core/_files/utils.py`
(stats/env/http/retry), enrich `schemas.py` (enum rank, feature columns), and a
vendored `model-services/_common/` (the model services intentionally cannot import
across the service boundary, so they get a small vendored copy rather than an
import).

| Cluster | Where it's duplicated | Single source of truth |
|---|---|---|
| **Feature schema** (`NUMERIC_COLS`/`CATEGORICAL_COLS`/`FEATURE_COLS`) — **5 copies** | `data_sim/common.py`, both `model-services/*/app.py`, `monitoring/data_loader.py`, `detection/drift_detector.py` (+ the now-fixed `config.feature_names`) | one constant in `schemas.py`/`config.py` for the agent side; `common.py` for the data-sim side; vendored copy for services |
| **`_RANK` severity order** | `decision.py` **and** `severity_classifier.py` (verbatim) | a `Severity.rank` property / `SEVERITY_RANK` next to the enum in `schemas.py` |
| **PSI + binning** | `drift_detector.py` (runtime) vs `build_reference_summary.py` (builder) — bin contract encoded twice | a shared `psi`/binning util owning the 10-bin contract |
| **percentile / median** | `anomaly_detector.py` (interp) vs `metrics.py` (nearest-rank) | a stats util exposing both **named** variants (they are intentionally different definitions) |
| **env parsing** | `config.py` (`_env_*`) vs raw `os.environ` in both `app.py` and `settings.py` (three different truthy sets!) | promote `config.py`'s helpers; one truthy convention |
| **HTTP `(connect, read)` timeout** | 6+ sites; magic `1.5`/`10`/`30` read values | a `settings.http_timeout(read=…)` accessor; named config fields; resolve at call time (see §2.4 / import-time freeze) |
| **retry/backoff loops** | `django_client.py`, `jenkins_client.py` ×2; configured-but-unused `verify_retries`/`verify_backoff_seconds` | one `retry()` / `poll_until()` helper parameterized by the existing config fields |
| **Python↔Django enums** | `ActionType`/`HealthStatus`/`Severity` vs Django `choices`, bridged by hand-kept `_ACTION_MAP`/`_HEALTH_MAP` | pick one wire casing and generate the Django `choices` + maps from it |
| **two model services** | `model_a` ≈ `model_b` (~290 lines, metrics.py byte-identical) | a shared `model-services/_common/` app-factory parameterized by env |
| **`_resolve_version`** | divergent copies in `actions_app` and `monitoring_app` views | one shared resolver |
| **error-envelope `{"error": {...}}`** | hand-built in ~6 views | an `error_response(code, message, status)` helper |

Mechanism note (per the request): where the helpers are pure functions (stats,
env, PSI, retry) a **shared util module** is the right fix — an abstract base
class would over-abstract. Where there is genuinely shared *behavior with
swappable internals* (executors, detectors, clients), the **abstract base /
Protocol + factory** of §2 is the right tool.

---

## 4. Data abstraction & privacy / encapsulation (DESIGN)

1. **Internal-topology exposure** — partially addressed (bug #17 gates
   `endpoint_url`/`port` and raw metric blobs behind `EXPOSE_INTERNAL_TOPOLOGY`).
   Next: flip the default to "hidden" in production via the same flag and route
   the active-model GET through the (currently dead) `ActiveModelSerializer`.
2. **Secure-by-default settings** — `DEBUG` defaults `True`, `SECRET_KEY` has an
   insecure default, and mutating endpoints (`/active-model`, `/actions`,
   `/metrics`) default to `AllowAny` with no security/CSRF middleware and no
   throttling. Recommended: `DEBUG=False` default, fail-fast on missing
   `SECRET_KEY` when not debugging, `IsAuthenticated` + `TokenAuthentication` by
   default, add `SecurityMiddleware`/`XFrameOptions`, and DRF throttles on the
   ingestion endpoints. (Deferred here because flipping these breaks the
   open-access local demo; do it alongside updating the demo scripts to
   authenticate.)
3. **Encapsulate invariants on the model** — `ModelVersion.is_active` is a plain
   writable boolean; the "single active" rule is now enforced at the DB (bug #15),
   but mutation should still funnel through `ActiveModelPointer.switch_to` only.
   Consider making `is_active` read-only outside the switch path.
4. **Private metric state** — the model-service `MetricsTracker` is well
   encapsulated, but `_total_requests`/`_total_errors` are dead private fields
   (never exposed): either surface lifetime totals in the snapshot or delete them.
5. **Validate `/predict` inputs** — add a Pydantic request model with bounds /
   enum checks (unknown `country`/`channel` currently silently one-hot to zeros and
   return a confident prediction; no payload-size limit).

---

## 5. Prioritized improvement backlog

**P0 — correctness/safety already shipped:** the 17 fixes in §1.

**P1 — highest leverage next:**
1. Unify the `Detector` / `Executor` / action-handler interfaces and introduce the
   three registries/factories (§2.1–2.3). One root-cause fix unlocks all three
   extension points and removes the `if/elif` chains.
2. Land the agent-side `utils.py` and collapse the §3 duplication clusters that are
   *correctness* risks first (feature schema, `_RANK`, PSI bin contract, env truthy
   sets) — a silent rename in any of these breaks drift detection or the audit
   trail across module boundaries.
3. Secure-by-default settings + demo-auth update (§4.2).

**P2 — structural cleanup:**
4. Collapse the two model services into a shared `_common/` app factory (§3).
5. Backend service layer for the Incident state machine + a shared `_resolve_version`
   and `error_response` helper (§2.5, §3).
6. Resolve HTTP timeouts/retries at call time via config (removes the import-time
   `config.settings` freeze that makes clients hard to test).

**P3 — robustness & product:**
7. Wire `verify_retries`/`verify_backoff_seconds` into `health_check` (configured
   but unused today — VERIFY does a single probe).
8. `/predict` request validation model (§4.5); add `/predict` & `/metrics` throttling.
9. Populate `Decision.requires_jenkins`/`jenkins_job` consistently with the active
   executor (today hardcoded `False`).
10. Surface or remove the dead `_total_requests`/`_total_errors` lifetime counters.
11. De-couple `data_sim` from writing directly into `agent_core/.../reference_window.json`
    and the service dirs (publish via an artifact path instead).
12. Fix `create_repo_structure.py`'s `_files` sentinel (it scaffolds a literal
    `_files/` dir) — currently moot but incorrect.
