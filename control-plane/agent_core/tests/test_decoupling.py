"""Unit tests for the §2 decoupling seams: detector registry, executor strategy,
and action-dispatch registry."""
import dataclasses
from contextlib import contextmanager

import config
from actions import dispatch
from actions.executors import (DirectExecutor, JenkinsExecutor, make_executor)
from detection.registry import (DetectionContext, Detector, default_detectors)
from schemas import (ActionType, Decision, HealthStatus, MetricSnapshot, Severity)


@contextmanager
def _settings(**overrides):
    original = config.settings
    config.settings = dataclasses.replace(original, **overrides)
    try:
        yield
    finally:
        config.settings = original


# ---- detector registry --------------------------------------------------

def test_default_detectors_satisfy_protocol_and_order():
    dets = default_detectors()
    assert [d.name for d in dets] == ["threshold", "anomaly", "drift"]
    assert all(isinstance(d, Detector) for d in dets)


def test_threshold_adapter_flags_via_context():
    [threshold, _anomaly, _drift] = default_detectors()
    ctx = DetectionContext(
        metrics=MetricSnapshot(model_name="model_a", model_version="1.0.0",
                               error_rate=0.6),
        reachable=True, health_status=HealthStatus.HEALTHY,
        consecutive_health_failures=0, inference_failure_rate=0.0)
    res = threshold.detect(ctx)
    assert any(d.metric == "error_rate" and d.anomaly_detected for d in res)


def test_anomaly_adapter_quiet_without_metrics():
    [_threshold, anomaly, _drift] = default_detectors()
    ctx = DetectionContext(metrics=None, reachable=False,
                           health_status=HealthStatus.UNHEALTHY,
                           consecutive_health_failures=1, inference_failure_rate=1.0)
    assert anomaly.detect(ctx) == []


# ---- executor strategy --------------------------------------------------

def test_make_executor_selects_by_config():
    with _settings(executor_type="direct"):
        assert isinstance(make_executor(), DirectExecutor)
    with _settings(executor_type="jenkins"):
        assert isinstance(make_executor(), JenkinsExecutor)
    with _settings(executor_type="unknown"):
        assert isinstance(make_executor(), DirectExecutor)  # safe default


def test_direct_executor_reports_success():
    res = DirectExecutor().switch("model_b", "because")
    assert res.ok and res.build_number is None


# ---- action dispatch ----------------------------------------------------

class _FakeRuntime:
    def __init__(self):
        self.active_model = "model_a"
        self.previous_model = None
        self.serving_enabled = True


def _decision(action):
    return Decision(action=action, severity=Severity.HIGH,
                    target_model="model_b", reason="r")


def test_dispatch_routes_each_action():
    rt = _FakeRuntime()
    assert dispatch.dispatch(_decision(ActionType.NO_OP), rt).action == ActionType.NO_OP
    assert dispatch.dispatch(_decision(ActionType.ALERT), rt).action == ActionType.ALERT
    # SWITCH routes to the switch handler, which flips the active pointer.
    res = dispatch.dispatch(_decision(ActionType.SWITCH_BACKUP), rt)
    assert res.action == ActionType.SWITCH_BACKUP and rt.active_model == "model_b"
