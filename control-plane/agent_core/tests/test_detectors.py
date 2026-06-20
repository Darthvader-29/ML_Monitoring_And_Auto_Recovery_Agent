"""Unit tests for the three detectors (detection_methods.md / roadmap §6)."""
import dataclasses
from contextlib import contextmanager

import config
from detection import threshold_detector
from detection.anomaly_detector import AnomalyDetector
from detection.drift_detector import DriftDetector
from monitoring import data_loader
from schemas import HealthStatus, MetricSnapshot


def _metrics(**kw) -> MetricSnapshot:
    base = dict(model_name="model_a", model_version="1.0.0")
    base.update(kw)
    return MetricSnapshot(**base)


@contextmanager
def _settings(**overrides):
    """Temporarily override the frozen config singleton (read at call-time)."""
    original = config.settings
    config.settings = dataclasses.replace(original, **overrides)
    try:
        yield
    finally:
        config.settings = original


# ---- threshold ----------------------------------------------------------

def test_threshold_fires_on_high_error_rate():
    res = threshold_detector.detect(
        _metrics(error_rate=0.6), reachable=True, health_status=HealthStatus.HEALTHY,
        consecutive_health_failures=0, inference_failure_rate=0.0)
    assert any(d.metric == "error_rate" and d.anomaly_detected for d in res)


def test_threshold_quiet_when_clean():
    res = threshold_detector.detect(
        _metrics(error_rate=0.0, p95_latency_ms=20, avg_confidence=0.9),
        reachable=True, health_status=HealthStatus.HEALTHY,
        consecutive_health_failures=0, inference_failure_rate=0.0)
    assert res == []


def test_threshold_flags_service_down():
    res = threshold_detector.detect(
        None, reachable=False, health_status=HealthStatus.UNHEALTHY,
        consecutive_health_failures=2, inference_failure_rate=1.0)
    assert any(d.metric == "service_up" for d in res)


# ---- confidence-based action thresholds (Phase 8 bonus, flag-gated) ------

def test_low_confidence_ratio_silent_when_gating_disabled():
    """Default behaviour (flag off): a high uncertain share emits no signal."""
    res = threshold_detector.detect(
        _metrics(error_rate=0.0, p95_latency_ms=20, avg_confidence=0.9),
        reachable=True, health_status=HealthStatus.HEALTHY,
        consecutive_health_failures=0, inference_failure_rate=0.0,
        low_confidence_ratio=0.9)
    assert not any(d.metric == "low_confidence_ratio" for d in res)


def test_low_confidence_ratio_fires_high_when_gating_enabled():
    with _settings(confidence_gating_enabled=True,
                   low_confidence_ratio_med=0.20, low_confidence_ratio_high=0.40):
        res = threshold_detector.detect(
            _metrics(error_rate=0.0, p95_latency_ms=20, avg_confidence=0.9),
            reachable=True, health_status=HealthStatus.HEALTHY,
            consecutive_health_failures=0, inference_failure_rate=0.0,
            low_confidence_ratio=0.5)
    sig = [d for d in res if d.metric == "low_confidence_ratio"]
    assert sig and sig[0].anomaly_detected and sig[0].score == 0.5


def test_low_confidence_ratio_quiet_below_med_edge():
    with _settings(confidence_gating_enabled=True, low_confidence_ratio_med=0.20):
        res = threshold_detector.detect(
            _metrics(error_rate=0.0, p95_latency_ms=20, avg_confidence=0.9),
            reachable=True, health_status=HealthStatus.HEALTHY,
            consecutive_health_failures=0, inference_failure_rate=0.0,
            low_confidence_ratio=0.10)
    assert not any(d.metric == "low_confidence_ratio" for d in res)


# ---- anomaly ------------------------------------------------------------

def test_anomaly_fires_on_spike_after_warmup():
    det = AnomalyDetector()
    for i in range(20):
        det.evaluate({"avg_latency_ms": 40 + (i % 3), "p95_latency_ms": 60,
                      "error_rate": 0.0, "inference_failure_rate": 0.0,
                      "avg_confidence": 0.85})
    res = det.evaluate({"avg_latency_ms": 900, "p95_latency_ms": 60,
                        "error_rate": 0.0, "inference_failure_rate": 0.0,
                        "avg_confidence": 0.85})
    assert any(d.anomaly_detected for d in res)


def test_anomaly_quiet_during_warmup():
    det = AnomalyDetector()
    res = det.evaluate({"avg_latency_ms": 900, "p95_latency_ms": 60,
                        "error_rate": 0.0, "inference_failure_rate": 0.0,
                        "avg_confidence": 0.85})
    assert res == []  # below W_MIN samples


# ---- drift --------------------------------------------------------------

def test_drift_quiet_on_baseline_batch():
    det = DriftDetector()
    res = det.evaluate_data_drift(data_loader.load_batch(drift=False))
    agg = [d for d in res if d.metric == "data_drift_aggregate"][0]
    assert not agg.anomaly_detected and agg.score == 0.0


def test_drift_fires_on_drift_batch():
    det = DriftDetector()
    res = det.evaluate_data_drift(data_loader.load_batch(drift=True))
    agg = [d for d in res if d.metric == "data_drift_aggregate"][0]
    assert agg.anomaly_detected and agg.score >= 0.30


def test_concept_drift_detects_accuracy_drop():
    det = DriftDetector()
    d = det.evaluate_concept_drift(0.72)
    assert d.anomaly_detected and d.score > 0.10
