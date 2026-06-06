"""DETECT — statistical anomaly detection (z-score / EWMA / IQR).

Phase 2 STUB: returns no signal. Real implementation lands in Phase 3
(detection_methods.md): EWMA-smoothed deviation of error/latency/confidence from
rolling norms, robust z-score and IQR outlier flags. Kept as a no-op so the
decision engine can already consume all three detector channels.
"""
from __future__ import annotations

from schemas import DetectionResult, MetricSnapshot  # noqa: F401  (Phase 3 API)


def detect(*_args, **_kwargs) -> list[DetectionResult]:
    return []
