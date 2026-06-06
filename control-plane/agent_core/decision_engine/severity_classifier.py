"""DECIDE — classify detector signals into a severity (LOW/MED/HIGH).

Encodes the band table from failure_scenarios.md §1.3. Each DetectionResult is
mapped to a Severity by the metric it concerns; the overall tick severity is the
worst across all signals (fail-stop priority, monitoring_and_metrics.md §6.2).
"""
from __future__ import annotations

import config
from schemas import DetectionResult, Severity

_RANK = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}


def _band_high_med_low(value: float, low: float, med: float, high: float) -> Severity:
    """Ascending metric (higher = worse): returns the band `value` falls in."""
    if value > high:
        return Severity.HIGH
    if value > med:
        return Severity.MEDIUM
    if value > low:
        return Severity.LOW
    return Severity.LOW


def classify_detection(d: DetectionResult) -> Severity:
    s = config.settings
    v = d.observed if d.observed is not None else 0.0

    if d.metric in ("error_rate", "inference_failure_rate"):
        # LOW 1-3%, MED 3-10%, HIGH >10% (§1.3)
        return _band_high_med_low(v, 0.01, 0.03, s.error_rate_threshold)
    if d.metric == "p95_latency_ms":
        # LOW 150-300, MED 300-800, HIGH >800
        return _band_high_med_low(v, _P95_LOW, _P95_MED, s.p95_latency_threshold_ms)
    if d.metric == "avg_confidence":
        # Descending metric (lower = worse): HIGH <0.55, MED <0.70, LOW <0.78
        if v < s.confidence_floor:
            return Severity.HIGH
        if v < _CONF_MED:
            return Severity.MEDIUM
        return Severity.LOW
    if d.metric == "service_up":
        # observed = consecutive failed health polls; >=2 => HIGH, 1 => MED
        return Severity.HIGH if v >= s.consecutive_failures_to_switch else Severity.MEDIUM
    return Severity.LOW


_P95_LOW, _P95_MED = 150.0, 300.0
_CONF_MED = 0.70


def classify(detections: list[DetectionResult]) -> Severity:
    """Overall severity = worst band across all signals (LOW if none)."""
    if not detections:
        return Severity.LOW
    return max((classify_detection(d) for d in detections), key=lambda sev: _RANK[sev])
