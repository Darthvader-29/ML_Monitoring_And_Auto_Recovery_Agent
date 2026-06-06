"""The autonomous agent — Observe -> Detect -> Decide -> Act -> Verify loop.

Phase 2 MVP (roadmap §4): one monitored model + one backup; threshold detection
only; the ACT step flips the active model via the DIRECT executor (in-memory, no
Jenkins); episodes are logged to stdout (durable Django audit arrives in Phase 4).

Run:  python agent.py [--ticks N] [--interval S] [--inject-drift]
(`make agent` runs this under venvd.) `--ticks` bounds the run for demos/CI; omit
it to run forever. Each phase is fault-isolated so an exception degrades the tick
rather than crashing the loop.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# --- path bootstrap: _files holds schemas/config; the parent holds the subpackages
_HERE = Path(__file__).resolve().parent          # .../agent_core/_files
_ROOT = _HERE.parent                             # .../agent_core
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config                                      # noqa: E402
from schemas import (ActionType, HealthStatus, MetricSnapshot,  # noqa: E402
                     Outcome, Severity)
from monitoring import data_loader, model_probe, prediction_probe  # noqa: E402
from detection import anomaly_detector, drift_detector, threshold_detector  # noqa: E402
from decision_engine import decision as decision_engine  # noqa: E402
from actions import alert, no_op, switch_model  # noqa: E402
from verification import health_check, rollback_guard  # noqa: E402
from clients import django_client  # noqa: E402

log = logging.getLogger("agent")
MODELS = ("model_a", "model_b")


@dataclass
class AgentRuntime:
    active_model: str = "model_a"
    previous_model: Optional[str] = None
    serving_enabled: bool = True
    consecutive_non_low: int = 0
    consecutive_health_failures: int = 0
    cooldown_remaining: int = 0
    recovery_attempts: int = 0
    inject_drift: bool = False
    endpoints: dict = field(default_factory=dict)

    def backup_model(self) -> str:
        return next(m for m in MODELS if m != self.active_model)

    def endpoint_for(self, name: str) -> str:
        return self.endpoints[name]


def _build_runtime(args) -> AgentRuntime:
    s = config.settings
    return AgentRuntime(
        active_model=args.active or "model_a",
        inject_drift=args.inject_drift,
        endpoints={"model_a": s.model_a_url, "model_b": s.model_b_url},
    )


def run_tick(runtime: AgentRuntime, dj, tick: int) -> dict:
    """One full Observe->Detect->Decide->Act->Verify cycle. Returns an episode dict."""
    active = runtime.active_model
    backup = runtime.backup_model()
    endpoint = runtime.endpoint_for(active)

    # ---- OBSERVE (predict first so /metrics reflects this tick) ----
    try:
        rows = data_loader.load_rows(drift=runtime.inject_drift)
        pred = prediction_probe.probe_predictions(endpoint, rows)
        health = model_probe.probe_health(endpoint)
        metrics = model_probe.probe_metrics(endpoint, active)
        backup_health = model_probe.probe_health(runtime.endpoint_for(backup))
    except Exception as exc:  # noqa: BLE001 — observe failure degrades the tick
        log.warning("observe error: %s", exc)
        health = model_probe.HealthProbe(False, HealthStatus.UNHEALTHY, False)
        metrics, backup_health = None, None
        pred = prediction_probe.PredictionProbe(0, 0, 0.0, 1.0)

    if not health.reachable or health.status == HealthStatus.UNHEALTHY:
        runtime.consecutive_health_failures += 1
    else:
        runtime.consecutive_health_failures = 0
    backup_healthy = bool(backup_health and backup_health.reachable
                          and backup_health.status == HealthStatus.HEALTHY)

    # ---- DETECT (threshold active; anomaly/drift stubbed until Phase 3) ----
    detections = threshold_detector.detect(
        metrics, reachable=health.reachable, health_status=health.status,
        consecutive_health_failures=runtime.consecutive_health_failures,
        inference_failure_rate=pred.inference_failure_rate)
    detections += anomaly_detector.detect()
    detections += drift_detector.detect()

    # ---- DECIDE (severity + anti-flap gating) ----
    from decision_engine import severity_classifier
    severity = severity_classifier.classify(detections)
    runtime.consecutive_non_low = (
        runtime.consecutive_non_low + 1 if severity != Severity.LOW else 0)
    confirmed = runtime.consecutive_non_low >= config.settings.confirm_n
    action_gated = (not confirmed) or runtime.cooldown_remaining > 0

    decision = decision_engine.make_decision(
        detections, active_model=active, backup_model=backup,
        backup_healthy=backup_healthy, action_gated=action_gated)

    # ---- ACT ----
    if decision.action == ActionType.SWITCH_BACKUP:
        result = switch_model.execute(decision, runtime, dj)
        if result.executed:
            runtime.cooldown_remaining = config.settings.cooldown_cycles
            runtime.recovery_attempts += 1
    elif decision.action == ActionType.DISABLE_PREDICTIONS:
        result = switch_model.execute(decision, runtime, dj)
    elif decision.action == ActionType.ALERT:
        result = alert.execute(decision)
    else:
        result = no_op.execute(decision)
    dj.post_metrics(metrics) if metrics else None
    action_id = dj.post_action(decision, result)

    # ---- VERIFY (only after a real switch) ----
    verification = None
    if decision.action == ActionType.SWITCH_BACKUP and result.executed:
        verification = health_check.verify(
            runtime.active_model, runtime.endpoint_for(runtime.active_model))
        rollback = rollback_guard.guard(verification, runtime, dj)
        dj.patch_action(action_id, verification)
        if rollback is not None:
            result = rollback

    if runtime.cooldown_remaining > 0:
        runtime.cooldown_remaining -= 1

    episode = {
        "tick": tick, "active": active, "now_active": runtime.active_model,
        "severity": severity.value, "action": decision.action.value,
        "outcome": result.outcome.value,
        "error_rate": round(metrics.error_rate, 3) if metrics else None,
        "backup_healthy": backup_healthy,
        "recovered": None if verification is None else verification.recovered,
    }
    log.info("tick=%(tick)d active=%(active)s sev=%(severity)s "
             "action=%(action)s outcome=%(outcome)s err=%(error_rate)s "
             "recovered=%(recovered)s", episode)
    return episode


def run(runtime: AgentRuntime, ticks: Optional[int], interval: float) -> list[dict]:
    dj = django_client.get_client()
    log.info("agent start: active=%s executor=%s interval=%ss ticks=%s",
             runtime.active_model, config.settings.executor_type, interval,
             ticks if ticks is not None else "inf")
    episodes, tick = [], 0
    try:
        while ticks is None or tick < ticks:
            tick += 1
            episodes.append(run_tick(runtime, dj, tick))
            if ticks is None or tick < ticks:
                time.sleep(interval)
    except KeyboardInterrupt:
        log.info("agent stopped (KeyboardInterrupt)")
    return episodes


def main() -> None:
    p = argparse.ArgumentParser(description="Autonomous ML monitoring agent")
    p.add_argument("--ticks", type=int, default=None, help="number of cycles (default: forever)")
    p.add_argument("--interval", type=float, default=None, help="seconds between cycles")
    p.add_argument("--active", default=None, help="starting active model (default model_a)")
    p.add_argument("--inject-drift", action="store_true", help="feed the drifted sample")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S")

    interval = args.interval if args.interval is not None \
        else config.settings.poll_interval_seconds
    run(_build_runtime(args), args.ticks, interval)


if __name__ == "__main__":
    main()
