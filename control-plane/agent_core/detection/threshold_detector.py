"""DETECT — static threshold breaches on system signals.

The only detector active in the MVP (roadmap Phase 2). It compares observed
metrics against the configured thresholds (failure_scenarios.md §1.3, surfaced via
config.py) and emits a DetectionResult for each signal that crosses its actionable
floor. Band classification (LOW/MED/HIGH) is left to the severity_classifier; this
module only answers "is this signal out of bounds, and by how much".
"""
from __future__ import annotations

from typing import Optional

import config
from schemas import DetectionResult, HealthStatus, MetricSnapshot

_DET = "threshold_detector"

# Actionable floors: below/under these a signal is not worth reporting. The HIGH
# edges live in config (failure_scenarios.md §1.3); these LOW floors gate noise.
_ERROR_RATE_FLOOR = 0.01
_P95_FLOOR_MS = 150.0
_CONFIDENCE_NOTABLE = 0.78   # mean confidence at/under this is worth a signal
_FAILURE_RATE_FLOOR = 0.01


def detect(
    metrics: Optional[MetricSnapshot],
    *,
    reachable: bool,
    health_status: HealthStatus,
    consecutive_health_failures: int,
    inference_failure_rate: float,
) -> list[DetectionResult]:
    s = config.settings
    results: list[DetectionResult] = []

    # --- Service health (unreachable or unhealthy) ---
    if not reachable or health_status == HealthStatus.UNHEALTHY:
        results.append(DetectionResult(
            detector=_DET, anomaly_detected=True, metric="service_up",
            observed=float(consecutive_health_failures),
            threshold=float(s.consecutive_failures_to_switch),
            message=(f"health check failing "
                     f"({consecutive_health_failures} consecutive)"),
        ))

    # --- Agent-observed inference failure rate (timeouts/refused the service
    #     may never have recorded) ---
    if inference_failure_rate > _FAILURE_RATE_FLOOR:
        results.append(DetectionResult(
            detector=_DET, anomaly_detected=True, metric="inference_failure_rate",
            observed=round(inference_failure_rate, 6), threshold=s.error_rate_threshold,
            message=f"inference_failure_rate {inference_failure_rate:.3f}",
        ))

    if metrics is None:
        return results

    # --- Error rate ---
    if metrics.error_rate > _ERROR_RATE_FLOOR:
        results.append(DetectionResult(
            detector=_DET, anomaly_detected=True, metric="error_rate",
            observed=round(metrics.error_rate, 6), threshold=s.error_rate_threshold,
            message=f"error_rate {metrics.error_rate:.3f}",
        ))

    # --- p95 latency ---
    if metrics.p95_latency_ms > _P95_FLOOR_MS:
        results.append(DetectionResult(
            detector=_DET, anomaly_detected=True, metric="p95_latency_ms",
            observed=round(metrics.p95_latency_ms, 3),
            threshold=s.p95_latency_threshold_ms,
            message=f"p95_latency_ms {metrics.p95_latency_ms:.1f}",
        ))

    # --- Confidence floor (note: lower is worse) ---
    if 0.0 < metrics.avg_confidence <= _CONFIDENCE_NOTABLE:
        results.append(DetectionResult(
            detector=_DET, anomaly_detected=True, metric="avg_confidence",
            observed=round(metrics.avg_confidence, 6), threshold=s.confidence_floor,
            message=f"avg_confidence {metrics.avg_confidence:.3f}",
        ))

    return results
