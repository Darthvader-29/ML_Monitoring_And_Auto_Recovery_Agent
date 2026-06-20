"""Unit tests for the VERIFY phase (health_check + rollback_guard)."""
import dataclasses
import types
from contextlib import contextmanager

import config
from schemas import ActionType, HealthStatus, VerificationResult
from verification import health_check, rollback_guard


@contextmanager
def _settings(**overrides):
    """Temporarily override the frozen config singleton (read at call-time)."""
    original = config.settings
    config.settings = dataclasses.replace(original, **overrides)
    try:
        yield
    finally:
        config.settings = original


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


def test_health_check_collapsed_confidence_not_recovered(monkeypatch):
    """A model that HAS served traffic but collapsed to 0.0 confidence must NOT pass
    verification, even though 0.0 is falsy (regression for the `not post_conf` bug)."""
    from monitoring import model_probe
    from schemas import MetricSnapshot
    monkeypatch.setattr(model_probe, "probe_health", lambda url: model_probe.HealthProbe(
        reachable=True, status=HealthStatus.HEALTHY, model_loaded=True))
    monkeypatch.setattr(model_probe, "probe_metrics", lambda url, name: MetricSnapshot(
        model_name=name, model_version="1.0.0", request_count=100,
        error_rate=0.0, avg_confidence=0.0))
    with _settings(verify_retries=1):  # single probe -> no backoff sleep
        v = health_check.verify("model_b", "http://b")
    assert v.recovered is False


def test_health_check_fresh_backup_without_traffic_is_recovered(monkeypatch):
    """A just-promoted, healthy backup that has served NO requests (request_count=0,
    so its 0.0 confidence/error are 'no data', not failure) must count as recovered
    on its health status — otherwise the agent flaps A->B->A forever."""
    from monitoring import model_probe
    from schemas import MetricSnapshot
    monkeypatch.setattr(model_probe, "probe_health", lambda url: model_probe.HealthProbe(
        reachable=True, status=HealthStatus.HEALTHY, model_loaded=True))
    monkeypatch.setattr(model_probe, "probe_metrics", lambda url, name: MetricSnapshot(
        model_name=name, model_version="1.0.0", request_count=0,
        error_rate=0.0, avg_confidence=0.0))
    with _settings(verify_retries=1):
        v = health_check.verify("model_b", "http://b")
    assert v.recovered is True


def test_health_check_recovers_after_retry(monkeypatch):
    """A backup that is UNHEALTHY on the first probe but HEALTHY on the next is
    reported recovered — VERIFY re-probes per verify_retries."""
    from monitoring import model_probe
    from schemas import MetricSnapshot
    calls = {"health": 0}

    def health(url):
        calls["health"] += 1
        if calls["health"] == 1:
            return model_probe.HealthProbe(False, HealthStatus.UNHEALTHY, False)
        return model_probe.HealthProbe(True, HealthStatus.HEALTHY, True)

    monkeypatch.setattr(model_probe, "probe_health", health)
    monkeypatch.setattr(model_probe, "probe_metrics", lambda url, name: MetricSnapshot(
        model_name=name, model_version="1.0.0", error_rate=0.0, avg_confidence=0.9))
    monkeypatch.setattr(health_check.time, "sleep", lambda _s: None)  # no real wait

    with _settings(verify_retries=3, verify_backoff_seconds=10.0):
        v = health_check.verify("model_b", "http://b")
    assert v.recovered is True
    assert calls["health"] == 2  # re-probed after the first failure


def test_health_check_exhausts_retries(monkeypatch):
    """If the model never recovers, VERIFY probes exactly verify_retries times and
    reports NOT recovered."""
    from monitoring import model_probe
    from schemas import MetricSnapshot
    calls = {"health": 0}

    def health(url):
        calls["health"] += 1
        return model_probe.HealthProbe(False, HealthStatus.UNHEALTHY, False)

    monkeypatch.setattr(model_probe, "probe_health", health)
    monkeypatch.setattr(model_probe, "probe_metrics", lambda url, name: MetricSnapshot(
        model_name=name, model_version="1.0.0", error_rate=0.0, avg_confidence=0.9))
    monkeypatch.setattr(health_check.time, "sleep", lambda _s: None)

    with _settings(verify_retries=2, verify_backoff_seconds=10.0):
        v = health_check.verify("model_b", "http://b")
    assert v.recovered is False
    assert calls["health"] == 2  # exactly verify_retries attempts
