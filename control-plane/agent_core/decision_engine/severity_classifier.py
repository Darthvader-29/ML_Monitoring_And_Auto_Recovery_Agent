"""DECIDE — classify detector signals into a severity (LOW/MED/HIGH).

Branches on the emitting detector (Phase 3 weighs all three channels):
  - threshold_detector: the system-signal band table (failure_scenarios.md §1.3);
  - anomaly_detector:    LOW spike, MED when sustained (detection_methods.md §3.8);
  - drift_detector:      data-drift aggregate by share-drifted and concept drift by
                         accuracy drop (§4.7 / §5.4); per-feature stays LOW (the
                         aggregate carries the actionable severity).
The overall tick severity is the worst across all signals (fail-stop priority,
monitoring_and_metrics.md §6.2).
"""
from __future__ import annotations

import config
from schemas import DetectionResult, Severity

_RANK = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3, Severity.CRITICAL: 4}

_P95_LOW, _P95_MED = 150.0, 300.0
_CONF_MED = 0.70
_DRIFT_SHARE_MED = 0.30   # share of features drifted => MED (detection_methods.md §4.7)
_DRIFT_SHARE_HIGH = 0.50


def _band_ascending(value: float, low: float, med: float, high: float) -> Severity:
    if value > high:
        return Severity.HIGH
    if value > med:
        return Severity.MEDIUM
    if value > low:
        return Severity.LOW
    return Severity.LOW


def _classify_threshold(d: DetectionResult) -> Severity:
    s = config.settings
    v = d.observed if d.observed is not None else 0.0
    if d.metric in ("error_rate", "inference_failure_rate"):
        return _band_ascending(v, 0.01, 0.03, s.error_rate_threshold)
    if d.metric == "p95_latency_ms":
        return _band_ascending(v, _P95_LOW, _P95_MED, s.p95_latency_threshold_ms)
    if d.metric == "avg_confidence":
        if v < s.confidence_floor:
            return Severity.HIGH
        if v < _CONF_MED:
            return Severity.MEDIUM
        return Severity.LOW
    if d.metric == "service_up":
        return Severity.HIGH if v >= s.consecutive_failures_to_switch else Severity.MEDIUM
    return Severity.LOW


def _classify_drift(d: DetectionResult) -> Severity:
    score = d.score if d.score is not None else 0.0
    if d.metric == "data_drift_aggregate":
        # share of drifted features (§4.7)
        if score >= _DRIFT_SHARE_HIGH:
            return Severity.HIGH
        if score >= _DRIFT_SHARE_MED:
            return Severity.MEDIUM
        return Severity.LOW
    if d.metric == "concept_drift_perf":
        # absolute accuracy drop (§5.4)
        if score >= 0.10:
            return Severity.HIGH
        if d.anomaly_detected:
            return Severity.MEDIUM
        return Severity.LOW
    # per-feature data_drift_psi stays LOW; the aggregate escalates.
    return Severity.LOW


def classify_detection(d: DetectionResult) -> Severity:
    if d.detector == "threshold_detector":
        return _classify_threshold(d)
    if d.detector == "anomaly_detector":
        return Severity.MEDIUM if "sustained" in d.message else Severity.LOW
    if d.detector == "drift_detector":
        return _classify_drift(d)
    return Severity.LOW


def classify(detections: list[DetectionResult]) -> Severity:
    """Overall severity = worst band across all signals (LOW if none breaching)."""
    breaching = [d for d in detections if d.anomaly_detected]
    if not breaching:
        return Severity.LOW
    return max((classify_detection(d) for d in breaching), key=lambda sev: _RANK[sev])
