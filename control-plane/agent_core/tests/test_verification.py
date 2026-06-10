"""Unit tests for the VERIFY phase (health_check + rollback_guard)."""
import types

from schemas import ActionType, HealthStatus, VerificationResult
from verification import health_check, rollback_guard


def test_rollback_guard_noop_on_recovery():
    v = VerificationResult(verified=True, model_checked="model_b", recovered=True)
    rt = types.SimpleNamespace(active_model="model_b", previous_model="model_a")
    assert rollback_guard.guard(v, rt) is None


def test_rollback_guard_reverts_and_escalates():
    v = VerificationResult(verified=True, model_checked="model_b", recovered=False)
    rt = types.SimpleNamespace(active_model="model_b", previous_model="model_a")
    res = rollback_guard.guard(v, rt)
    assert res.action == ActionType.ROLLBACK
    assert rt.active_model == "model_a"      # reverted
    assert v.escalate_to_human is True


def test_rollback_guard_escalates_without_target():
    v = VerificationResult(verified=True, model_checked="model_a", recovered=False)
    rt = types.SimpleNamespace(active_model="model_a", previous_model=None)
    res = rollback_guard.guard(v, rt)
    assert res.action == ActionType.ALERT and v.escalate_to_human is True


def test_health_check_recovered(monkeypatch):
    from monitoring import model_probe
    from schemas import MetricSnapshot
    monkeypatch.setattr(model_probe, "probe_health", lambda url: model_probe.HealthProbe(
        reachable=True, status=HealthStatus.HEALTHY, model_loaded=True))
    monkeypatch.setattr(model_probe, "probe_metrics", lambda url, name: MetricSnapshot(
        model_name=name, model_version="1.0.0", error_rate=0.0, avg_confidence=0.9))
    v = health_check.verify("model_b", "http://b")
    assert v.recovered is True
