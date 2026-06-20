"""VERIFY — confirm the post-action active model is actually healthy.

After a switch (or any act), re-probe the now-active model and decide whether the
problem is resolved (agent_logic.md VERIFY phase). "Recovered" means: health OK,
error rate within threshold, and confidence not collapsed. Retry/backoff hardening
arrives in Phase 7; the MVP does a single confirmation probe.
"""
from __future__ import annotations

from typing import Optional

import config
from schemas import HealthStatus, MetricSnapshot, VerificationResult

from monitoring import model_probe


def verify(model_name: str, endpoint_url: str,
           baseline: Optional[MetricSnapshot] = None) -> VerificationResult:
    s = config.settings
    health = model_probe.probe_health(endpoint_url)
    metrics = model_probe.probe_metrics(endpoint_url, model_name)

    post_error = metrics.error_rate if metrics else None
    post_conf = metrics.avg_confidence if metrics else None

    healthy = health.reachable and health.status == HealthStatus.HEALTHY
    error_ok = post_error is None or post_error <= s.error_rate_threshold
    # Only a *missing* metric (None) may skip the confidence gate. A collapsed
    # confidence of 0.0 is falsy but must NOT count as recovered — using
    # `not post_conf` here let a fully-collapsed model pass verification.
    conf_ok = post_conf is None or post_conf >= s.confidence_floor
    recovered = healthy and error_ok and conf_ok

    return VerificationResult(
        verified=True,
        model_checked=model_name,
        baseline_error_rate=baseline.error_rate if baseline else None,
        post_action_error_rate=post_error,
        baseline_confidence=baseline.avg_confidence if baseline else None,
        post_action_confidence=post_conf,
        post_action_health=health.status,
        recovered=recovered,
        message=("recovered: backup healthy and within thresholds" if recovered
                 else "NOT recovered: post-action metrics still breaching"),
    )
