"""DETECT — a uniform detector interface + registry.

The three detection channels historically had three different call shapes
(`threshold_detector.detect(**kwargs)`, `AnomalyDetector.evaluate(dict)`,
`DriftDetector.evaluate_data_drift(rows)`), which forced the agent loop to wire
each one by hand. This module gives them a single `Detector` contract —
`detect(ctx) -> list[DetectionResult]` over a shared `DetectionContext` — and a
`default_detectors()` factory. Adding a detector becomes registration, not editing
`run_tick`.

The underlying detector classes/functions are unchanged (and still unit-tested
directly); these are thin adapters onto the common contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from schemas import DetectionResult, HealthStatus, MetricSnapshot

from detection import threshold_detector
from detection.anomaly_detector import AnomalyDetector
from detection.drift_detector import DriftDetector


@dataclass
class DetectionContext:
    """Everything a detector might need for one tick, assembled by the Observe phase."""
    metrics: Optional[MetricSnapshot]
    reachable: bool
    health_status: HealthStatus
    consecutive_health_failures: int
    inference_failure_rate: float
    low_confidence_ratio: float = 0.0
    drift_batch: list[dict] = field(default_factory=list)


@runtime_checkable
class Detector(Protocol):
    name: str

    def detect(self, ctx: DetectionContext) -> list[DetectionResult]: ...


class ThresholdDetector:
    """Static-threshold breaches on system signals (stateless)."""
    name = "threshold"

    def detect(self, ctx: DetectionContext) -> list[DetectionResult]:
        return threshold_detector.detect(
            ctx.metrics, reachable=ctx.reachable, health_status=ctx.health_status,
            consecutive_health_failures=ctx.consecutive_health_failures,
            inference_failure_rate=ctx.inference_failure_rate,
            low_confidence_ratio=ctx.low_confidence_ratio)


class AnomalyDetectorAdapter:
    """Robust-z/IQR/EWMA anomalies vs each metric's own history (stateful)."""
    name = "anomaly"

    def __init__(self, detector: Optional[AnomalyDetector] = None) -> None:
        self._det = detector or AnomalyDetector()

    def detect(self, ctx: DetectionContext) -> list[DetectionResult]:
        if ctx.metrics is None:
            return []
        m = ctx.metrics
        return self._det.evaluate({
            "error_rate": m.error_rate,
            "avg_latency_ms": m.avg_latency_ms,
            "p95_latency_ms": m.p95_latency_ms,
            "inference_failure_rate": ctx.inference_failure_rate,
            "avg_confidence": m.avg_confidence,
        })


class DriftDetectorAdapter:
    """Data-drift (PSI/KS/chi-square) over a large batch (stateful window)."""
    name = "drift"

    def __init__(self, detector: Optional[DriftDetector] = None) -> None:
        self._det = detector or DriftDetector()

    def detect(self, ctx: DetectionContext) -> list[DetectionResult]:
        return self._det.evaluate_data_drift(ctx.drift_batch)


def default_detectors() -> list[Detector]:
    """The standard threshold + anomaly + drift stack, in evaluation order.
    Each instance is stateful across ticks, so build this ONCE per agent run."""
    return [ThresholdDetector(), AnomalyDetectorAdapter(), DriftDetectorAdapter()]
