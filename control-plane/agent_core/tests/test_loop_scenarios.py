"""End-to-end scenario tests — failure_scenarios.md cases driven through the loop.

Each test scripts the OBSERVE probes (no network) and runs agent.run_tick across
ticks, asserting the agent's behaviour: anti-flap confirmation, A->B switch on a
confirmed HIGH, fail-closed when the backup is unhealthy.
"""
import importlib

import pytest

agent = importlib.import_module("agent")
from monitoring import model_probe, prediction_probe  # noqa: E402
from schemas import HealthStatus, MetricSnapshot  # noqa: E402


class FakeDjango:
    def post_metrics(self, *a, **k): pass
    def get_active_model(self): return None
    def set_active_model(self, *a, **k): pass
    def post_action(self, *a, **k): return 1
    def patch_action(self, *a, **k): pass


def _runtime():
    rt = agent.AgentRuntime(active_model="model_a",
                            endpoints={"model_a": "http://a", "model_b": "http://b"})
    return rt


def _patch(monkeypatch, *, a_error, b_healthy=True):
    """Script the probes: model_a serves `a_error` error rate; model_b health per flag."""
    def health(url):
        healthy = HealthStatus.HEALTHY
        if url == "http://b" and not b_healthy:
            return model_probe.HealthProbe(False, HealthStatus.UNHEALTHY, False)
        return model_probe.HealthProbe(True, healthy, True)

    def metrics(url, name):
        err = a_error if url == "http://a" else 0.0
        return MetricSnapshot(model_name=name, model_version="1.0.0",
                              error_rate=err, avg_confidence=0.9, p95_latency_ms=20)

    def preds(url, rows):
        err = a_error if url == "http://a" else 0.0
        return prediction_probe.PredictionProbe(
            attempted=10, failed=int(err * 10), avg_confidence=0.9,
            inference_failure_rate=err)

    monkeypatch.setattr(model_probe, "probe_health", health)
    monkeypatch.setattr(model_probe, "probe_metrics", metrics)
    monkeypatch.setattr(prediction_probe, "probe_predictions", preds)


def _detectors():
    # The unified detector registry (threshold + anomaly + drift), stateful per run.
    return agent.default_detectors()


def test_A1_error_spike_switches_after_confirm(monkeypatch):
    """A1: sustained HIGH error -> alert (tick1, unconfirmed) -> switch A->B (tick2)."""
    _patch(monkeypatch, a_error=0.6)
    rt, dj, det = _runtime(), FakeDjango(), _detectors()
    e1 = agent.run_tick(rt, dj, det, 1)
    assert e1["action"] == "alert" and rt.active_model == "model_a"  # anti-flap
    e2 = agent.run_tick(rt, dj, det, 2)
    assert e2["action"] == "switch_backup" and rt.active_model == "model_b"


def test_N1_transient_spike_does_not_flap(monkeypatch):
    """N1: a single-cycle HIGH then clean -> no switch ever."""
    rt, dj, det = _runtime(), FakeDjango(), _detectors()
    _patch(monkeypatch, a_error=0.6)
    agent.run_tick(rt, dj, det, 1)            # HIGH but unconfirmed -> alert
    _patch(monkeypatch, a_error=0.0)
    e2 = agent.run_tick(rt, dj, det, 2)       # back to baseline
    assert rt.active_model == "model_a" and e2["action"] in ("no_op", "alert")


def test_S4_backup_unhealthy_fails_closed(monkeypatch):
    """S4: HIGH on active but backup unhealthy -> disable predictions, no switch."""
    _patch(monkeypatch, a_error=0.6, b_healthy=False)
    rt, dj, det = _runtime(), FakeDjango(), _detectors()
    agent.run_tick(rt, dj, det, 1)            # unconfirmed
    e2 = agent.run_tick(rt, dj, det, 2)       # confirmed, but backup unhealthy
    assert e2["action"] == "disable_predictions"
    assert rt.active_model == "model_a" and rt.serving_enabled is False


def test_healthy_steady_state_is_noop(monkeypatch):
    _patch(monkeypatch, a_error=0.0)
    rt, dj, det = _runtime(), FakeDjango(), _detectors()
    for t in range(1, 4):
        e = agent.run_tick(rt, dj, det, t)
        assert e["action"] == "no_op"
    assert rt.active_model == "model_a"
