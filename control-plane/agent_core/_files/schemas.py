# control-plane/agent_core/schemas.py
#
# Shared typed objects the agent passes between loop phases (Observe → Detect →
# Decide → Act → Verify) IN MEMORY. The JSON the agent serializes for Django must
# match these field-for-field.
#
# This module is the SINGLE SOURCE OF TRUTH for these types — it is the canonical
# definition transcribed from docs/api_contracts.md §D (the authoritative HTTP
# contract). Do not redefine these shapes elsewhere; import them from here.
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    """Timezone-aware UTC now. `datetime.utcnow()` is deprecated (3.12+) and
    returns a NAIVE datetime, which serializes without an offset and is then
    ambiguous to the tz-aware Django backend (USE_TZ=True)."""
    return datetime.now(timezone.utc)


# ---- Base ---------------------------------------------------------------

class _Schema(BaseModel):
    """Shared base for every agent schema.

    The API contract (api_contracts.md §A/§D) mandates field names like
    `model_name` / `model_version` / `model_loaded` / `model_checked`. Pydantic v2
    reserves the `model_` prefix for its own internals and warns on such fields, so
    we disable that protected namespace here once for all schemas — the wire names
    are load-bearing and must not change.
    """
    model_config = ConfigDict(protected_namespaces=())


# ---- Enumerations -------------------------------------------------------

class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ActionType(str, Enum):
    NO_OP = "no_op"
    ALERT = "alert"
    ROLLBACK = "rollback"
    SWITCH_BACKUP = "switch_backup"
    RETRAIN = "retrain"
    DISABLE_PREDICTIONS = "disable_predictions"


class Outcome(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---- 1. OBSERVE ---------------------------------------------------------

class MetricSnapshot(_Schema):
    """Rolling operational metrics for one model.
    JSON-identical to the Django `MetricSnapshotSerializer` body of POST /api/metrics."""
    model_name: str
    model_version: str
    request_count: int = 0
    error_count: int = 0
    error_rate: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    avg_confidence: float = 0.0
    accuracy: Optional[float] = None
    status: HealthStatus = HealthStatus.HEALTHY
    window: str = "5m"
    source: str = "agent"
    timestamp: datetime = Field(default_factory=_utcnow)


class Observation(_Schema):
    """One full sweep of a single model: health + metrics + a sample prediction.
    Built in the Observe phase from /health, /metrics and /predict probes."""
    model_name: str
    endpoint_url: str
    reachable: bool                       # False => probe timed out / connection error
    health_status: HealthStatus
    model_loaded: bool
    uptime_seconds: Optional[float] = None
    metrics: MetricSnapshot
    sample_prediction: Optional[float] = None
    sample_confidence: Optional[float] = None
    observed_at: datetime = Field(default_factory=_utcnow)


# ---- 2. DETECT ----------------------------------------------------------

class DetectionResult(_Schema):
    """Output of a detector (threshold / anomaly / drift).
    Serialized into ActionLog.detection_signal."""
    detector: str                         # "threshold_detector" | "anomaly_detector" | "drift_detector"
    anomaly_detected: bool
    metric: Optional[str] = None          # e.g. "error_rate", "p95_latency_ms", "avg_confidence"
    observed: Optional[float] = None
    threshold: Optional[float] = None
    window: str = "5m"
    score: Optional[float] = None         # drift/anomaly score when applicable
    message: str = ""
    detected_at: datetime = Field(default_factory=_utcnow)


# ---- 3. DECIDE ----------------------------------------------------------

class Decision(_Schema):
    """Output of the decision engine: severity + chosen action.
    Maps directly onto the POST /api/actions body."""
    action: ActionType
    severity: Severity
    target_model: str
    reason: str
    triggered_by: str = "agent"
    detection_signal: Optional[DetectionResult] = None
    requires_jenkins: bool = False
    jenkins_job: Optional[str] = None     # "switch_active_model" | "rollback_model" | "deploy_model"
    jenkins_params: dict[str, Any] = Field(default_factory=dict)
    decided_at: datetime = Field(default_factory=_utcnow)


# ---- 4. ACT -------------------------------------------------------------

class ActionResult(_Schema):
    """Result of executing a Decision (e.g. triggering Jenkins + logging to Django)."""
    action: ActionType
    target_model: str
    executed: bool
    outcome: Outcome = Outcome.PENDING
    action_log_id: Optional[int] = None   # id returned by POST /api/actions
    jenkins_job: Optional[str] = None
    jenkins_build_number: Optional[int] = None
    jenkins_build_url: Optional[str] = None
    message: str = ""
    executed_at: datetime = Field(default_factory=_utcnow)


# ---- 5. VERIFY ----------------------------------------------------------

class VerificationResult(_Schema):
    """Post-recovery validation. Serialized into the PATCH /api/actions/{id} body."""
    verified: bool
    model_checked: str
    baseline_error_rate: Optional[float] = None
    post_action_error_rate: Optional[float] = None
    baseline_confidence: Optional[float] = None
    post_action_confidence: Optional[float] = None
    post_action_health: Optional[HealthStatus] = None
    recovered: bool = False
    escalate_to_human: bool = False
    message: str = ""
    checked_at: datetime = Field(default_factory=_utcnow)
