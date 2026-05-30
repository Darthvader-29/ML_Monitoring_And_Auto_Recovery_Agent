# 📏 Canonical Conventions — Schemas, Enums & Defaults

> **This document is the single source of truth.** Where any other doc disagrees
> with the values, field names, casing, or schemas defined here, **this document
> wins** and the other doc is to be corrected. All wire formats (HTTP JSON),
> pydantic schemas (`agent_core/schemas.py`), and Django model choices must use
> exactly the names and casing below.

---

## 1. Enumerations

Enum **wire values** (the literal strings sent over HTTP / stored in the DB) are
fixed below. Casing is normative.

### 1.1 `HealthStatus` — model health (reported & derived)

Used by: model service `GET /health.health_status`, `MetricSnapshot.health_status`,
`Observation.health_status`, the dashboard, and the agent.

| Value | Meaning |
|-------|---------|
| `HEALTHY` | Operating within baseline on all signals. |
| `DEGRADED` | One or more signals breached but the model is still serving. |
| `CRITICAL` | Severe breach; model is unsafe/unreliable (or failing). |
| `UNKNOWN` | State cannot be determined — e.g. the agent's probe timed out / connection refused (**"unreachable" maps to `UNKNOWN`**). |

- **Casing: UPPERCASE.** There is no `unhealthy` and no lowercase variant.
- A model service never reports `UNKNOWN` about itself; the **agent** assigns
  `UNKNOWN` when a probe fails (`reachable = false`).

### 1.2 `Severity` — incident/decision severity

Used by: `severity_classifier.py`, `Decision.severity`, `ActionLog.severity`,
`Incident.severity`.

| Value | Meaning |
|-------|---------|
| `NONE` | No actionable signal this tick. **Transient only** — never persisted on an executed action. |
| `LOW` | Minor / likely-transient degradation. |
| `MEDIUM` | Sustained or moderate degradation. |
| `HIGH` | Severe / persistent degradation requiring recovery. |

- **Casing: UPPERCASE.** There is **no `CRITICAL` severity** — `CRITICAL`
  belongs to `HealthStatus` only. Keeping them disjoint avoids confusion.
- Stored severity (`ActionLog`, `Incident`) is one of `LOW | MEDIUM | HIGH`
  (`NONE` is never written, since a `no_op` with `NONE` produces no incident).

### 1.3 `ActionType` — recovery action

Used by: `policy_rules.py`, `Decision.action`, `ActionResult.action`,
`ActionLog.action`. Module/file and Jenkins-job names are **separate** from these
values (see §4).

| Value | Meaning | Reversible by |
|-------|---------|---------------|
| `no_op` | Do nothing; keep observing. | — |
| `alert` | Raise an alert only; take no corrective action. | — |
| `switch_to_backup` | Route traffic from the active model to the backup. | `switch_to_backup` (back) |
| `rollback` | Revert the active model to its last `STABLE` version. | `rollback` (forward) |
| `retrain` | Trigger a (simulated) retraining + redeploy of the model. | `rollback` |
| `disable_predictions` | Temporarily stop serving predictions (fail safe). | `enable_predictions` |
| `enable_predictions` | Re-enable a previously disabled model. | `disable_predictions` |

- **Casing: lowercase `snake_case`.** Not `SWITCH`, not `switch_backup`, not
  `switch_model`, not `retrain_deploy`.
- `Decision.target_model` carries the operand (e.g. `"model_b"` for a switch, or
  the version to restore for a rollback).

### 1.4 `Outcome` — execution outcome of an action

Used by: `ActionResult.outcome`, `ActionLog.outcome`.

| Value | Meaning |
|-------|---------|
| `pending` | Action submitted/queued; result not yet known. |
| `success` | Action executed and (where applicable) verified successfully. |
| `failed` | Action failed to execute or failed verification. |
| `reverted` | Action was rolled back by the agent (e.g. verification failed). |
| `skipped` | Action was a no-op or suppressed (cooldown / dry-run). |

- **Casing: lowercase.** There is no `DONE`, `queued`, or `logged` value —
  map those onto `success` / `pending` respectively.

### 1.5 `signal_type` — per-detector signal key

Stable string keys downstream policies match on (defined in `detection_methods.md`):

| `detector` | `signal_type` values |
|------------|----------------------|
| `threshold` | `error_rate`, `avg_latency_ms`, `p95_latency_ms`, `inference_failure_rate`, `confidence_floor` |
| `anomaly` | `zscore_spike`, `robust_zscore_spike`, `iqr_outlier`, `ewma_shift`, `iforest_outlier` |
| `drift` | `data_drift_psi`, `data_drift_ks`, `data_drift_chi2`, `data_drift_aggregate`, `concept_drift_perf` |

`severity_hint` on a `DetectionResult` uses the **`Severity`** values
(`NONE/LOW/MEDIUM/HIGH`, UPPERCASE) — it is a *hint* the decision engine may
override.

---

## 2. Canonical pydantic schemas (`agent_core/schemas.py`)

These are the authoritative shapes the agent passes between loop phases. HTTP
bodies and Django serializers must match these field names/types.

```python
from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

HealthStatus = Literal["HEALTHY", "DEGRADED", "CRITICAL", "UNKNOWN"]
Severity     = Literal["NONE", "LOW", "MEDIUM", "HIGH"]
ActionType   = Literal["no_op", "alert", "switch_to_backup", "rollback",
                        "retrain", "disable_predictions", "enable_predictions"]
Outcome      = Literal["pending", "success", "failed", "reverted", "skipped"]


# ---- OBSERVE ----
class MetricSnapshot(BaseModel):
    """Rolling metrics for one model over `window_seconds`.
    JSON-identical to the body of POST /api/metrics and the Django MetricSnapshot row."""
    model_name: str
    model_version: str
    # system
    request_count: int = 0
    error_count: int = 0
    error_rate: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    inference_failure_rate: float = 0.0
    # performance
    avg_confidence: Optional[float] = None
    accuracy: Optional[float] = None
    f1: Optional[float] = None
    rmse: Optional[float] = None
    # data quality
    missing_rate: float = 0.0
    out_of_range_rate: float = 0.0
    # drift
    overall_drift_score: float = 0.0
    drifted_feature_count: int = 0
    # status / meta
    health_status: HealthStatus = "HEALTHY"
    window_seconds: int = 300
    source: str = "agent"
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Observation(BaseModel):
    """One full probe sweep of a single model: health + metrics + a sample prediction."""
    model_name: str
    endpoint_url: str
    reachable: bool                      # False => probe timed out / connection refused => health UNKNOWN
    health_status: HealthStatus
    model_loaded: bool
    uptime_seconds: Optional[float] = None
    metrics: MetricSnapshot
    sample_prediction: Optional[float] = None
    sample_confidence: Optional[float] = None
    observed_at: datetime = Field(default_factory=datetime.utcnow)


# ---- DETECT ----
class DetectionResult(BaseModel):
    """Output of ONE detector evaluation (threshold / anomaly / drift).
    A tick produces a list of these. Serialized into ActionLog.detection_signal."""
    detector: Literal["threshold", "anomaly", "drift"]
    signal_type: str                     # see conventions §1.5
    score: float                         # statistic compared against threshold
    threshold: float
    breached: bool
    drifted_features: list[str] = Field(default_factory=list)   # drift only
    severity_hint: Severity = "NONE"
    evidence: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class DetectionSummary(BaseModel):
    """The per-tick AGGREGATE the decision engine consumes (one per tick).
    Built by folding the tick's list[DetectionResult]."""
    ts: datetime
    results: list[DetectionResult] = Field(default_factory=list)
    threshold_breaches: set[str] = Field(default_factory=set)   # e.g. {"ERROR_RATE_HIGH"}
    anomaly_flag: bool = False
    anomaly_score: float = 0.0
    drift_flag: bool = False
    drift_score: float = 0.0
    signal_type: Literal["NONE", "THRESHOLD", "ANOMALY", "DRIFT", "MIXED"] = "NONE"


# ---- DECIDE ----
class Decision(BaseModel):
    """Severity + chosen action. Maps onto POST /api/actions."""
    action: ActionType
    severity: Severity
    signal_type: Literal["NONE", "THRESHOLD", "ANOMALY", "DRIFT", "MIXED"] = "NONE"
    persistence: Literal["TRANSIENT", "PERSISTENT", "CLEARED"] = "TRANSIENT"
    reason: str
    target_model: Optional[str] = None
    confidence: float = 0.0
    incident_id: Optional[str] = None
    detection_signal: Optional[DetectionResult] = None
    requires_jenkins: bool = False
    jenkins_job: Optional[str] = None    # see §4
    jenkins_params: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    decided_at: datetime = Field(default_factory=datetime.utcnow)


# ---- ACT ----
class ActionResult(BaseModel):
    action: ActionType
    target_model: Optional[str] = None
    executed: bool
    outcome: Outcome = "pending"
    action_log_id: Optional[int] = None
    jenkins_job: Optional[str] = None
    jenkins_build_number: Optional[int] = None
    jenkins_build_url: Optional[str] = None
    message: str = ""
    executed_at: datetime = Field(default_factory=datetime.utcnow)


# ---- VERIFY ----
class VerificationResult(BaseModel):
    verified: bool
    model_checked: str
    baseline_error_rate: Optional[float] = None
    post_action_error_rate: Optional[float] = None
    baseline_confidence: Optional[float] = None
    post_action_confidence: Optional[float] = None
    post_action_health: Optional[HealthStatus] = None
    recovered: bool = False
    escalate_to_human: bool = False
    outcome: Outcome = "pending"
    message: str = ""
    checked_at: datetime = Field(default_factory=datetime.utcnow)
```

### Schema flow through the loop

```
Observe   →  Observation { metrics: MetricSnapshot }    ──POST /api/metrics──▶ Django
Detect    →  list[DetectionResult]  ─fold→  DetectionSummary
Decide    →  Decision (Severity + ActionType)
Act       →  ActionResult                               ──POST /api/actions──▶ Django (outcome=pending)
                 └─ if requires_jenkins: ──buildWithParameters──▶ Jenkins
Verify    →  VerificationResult                         ──PATCH /api/actions/{id}──▶ Django (outcome=success/failed/reverted)
                 └─ if recovered: ──POST /api/active-model──▶ Django registry update
```

---

## 3. Default parameters & thresholds

The canonical defaults (mirrors `agent_core/config.py`). Other docs must not
contradict these values.

| Parameter | Default | Used by |
|-----------|---------|---------|
| `AGENT_TICK_INTERVAL_SECONDS` | `30` | agent loop |
| `ROLLING_WINDOW_SIZE` (ticks) | `10` (≈5 min) | observe/detect |
| `METRIC_WINDOW_SECONDS` | `300` | `MetricSnapshot.window_seconds` |
| `ERROR_RATE_THRESHOLD` | `0.10` | threshold_detector |
| `P95_LATENCY_THRESHOLD_MS` | `500` | threshold_detector |
| `AVG_LATENCY_THRESHOLD_MS` | `300` | threshold_detector |
| `CONFIDENCE_FLOOR` | `0.60` | threshold_detector |
| `INFERENCE_FAILURE_RATE_THRESHOLD` | `0.05` | threshold_detector |
| `ZSCORE_K` (anomaly) | `3.0` | anomaly_detector |
| `PSI_MODERATE` / `PSI_SIGNIFICANT` | `0.10` / `0.25` | drift_detector |
| `KS_ALPHA` | `0.05` | drift_detector |
| `DRIFT_SCORE_THRESHOLD` | `0.25` | drift_detector |
| `ACCURACY_DROP_ABS` | `0.05` | concept-drift |
| `MIN_CONSECUTIVE_BREACHES` (debounce) | `3` | decision engine |
| `MIN_ACTION_CONFIDENCE` | `0.60` | decision engine (below ⇒ downgrade to `alert`) |
| `ACTION_COOLDOWN_SECONDS` | `300` | decision engine |
| `MAX_ACTIONS_PER_HOUR` (rate limit) | `6` | safety guard |
| `VERIFY_MAX_ATTEMPTS` | `2` (then escalate) | verification |

> If a doc states a different default, **this table wins**; update the doc.

---

## 4. Names that are NOT enum values

Keep these distinct from the `ActionType` wire values — they are
**identifiers**, not action strings:

| Kind | Names |
|------|-------|
| Agent action modules | `actions/switch_model.py`, `actions/alert.py`, `actions/no_op.py` |
| Jenkins jobs | `deploy_model`, `switch_active_model`, `rollback_model` |
| `Decision.jenkins_job` values | `"switch_active_model"`, `"rollback_model"`, `"deploy_model"` |

Mapping from `ActionType` → Jenkins job:

| `ActionType` | `jenkins_job` |
|--------------|---------------|
| `switch_to_backup` | `switch_active_model` |
| `rollback` | `rollback_model` |
| `retrain` | `deploy_model` |
| `no_op`, `alert`, `disable_predictions`, `enable_predictions` | *(none — handled by the agent/Django directly)* |

---

## 5. HTTP conventions (summary)

- **Base URLs** come from `.env` (e.g. `http://model_a:8001`, `http://backend:8000`).
- **Content type:** `application/json` for all bodies.
- **Timestamps:** ISO-8601 UTC.
- **Casing recap:** `HealthStatus` & `Severity` are **UPPERCASE**; `ActionType`
  & `Outcome` are **lowercase snake_case**. This split is intentional and fixed.

See `api_contracts.md` for full per-endpoint request/response detail (which
conforms to the schemas above).
