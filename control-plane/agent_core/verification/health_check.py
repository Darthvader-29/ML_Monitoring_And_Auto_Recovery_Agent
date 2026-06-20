"""VERIFY — confirm the post-action active model is actually healthy.

After a switch (or any act), re-probe the now-active model and decide whether the
problem is resolved (agent_logic.md VERIFY phase). "Recovered" means: health OK,
error rate within threshold, and confidence not collapsed. A freshly-promoted
backup may need a moment to warm up, so VERIFY re-probes up to
`config.verify_retries` times, waiting `config.verify_backoff_seconds` between
attempts, and returns as soon as the model looks recovered.
"""
from __future__ import annotations

import time
from typing import Optional

import config
from schemas import HealthStatus, MetricSnapshot, VerificationResult

from monitoring import model_probe


def _probe_once(model_name: str, endpoint_url: str,
                baseline: Optional[MetricSnapshot]) -> VerificationResult:
    """One confirmation probe of the now-active model -> a VerificationResult."""
    s = config.settings
    health = model_probe.probe_health(endpoint_url)
    metrics = model_probe.probe_metrics(endpoint_url, model_name)

    # A just-promoted backup has typically served NO requests at verify time, so its
    # reported error_rate/avg_confidence are 0.0 by *absence of data*, not by
    # failure. Only treat them as real observations once the model has actually
    # served traffic — otherwise a healthy fresh backup would be failed on a 0.0
    # confidence that merely means "no data yet" (causing an immediate rollback /
    # an A->B->A flap, as end-to-end testing showed).
    has_traffic = metrics is not None and metrics.request_count > 0
    post_error = metrics.error_rate if has_traffic else None
    post_conf = metrics.avg_confidence if has_traffic else None

    healthy = health.reachable and health.status == HealthStatus.HEALTHY
    error_ok = post_error is None or post_error <= s.error_rate_threshold
    # Only a *not-yet-observed* metric (None) may skip the confidence gate. A
    # collapsed confidence of 0.0 on a model that HAS served traffic is falsy but
    # must NOT count as recovered — using `not post_conf` here let a fully-collapsed
    # model pass verification.
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


def verify(model_name: str, endpoint_url: str,
           baseline: Optional[MetricSnapshot] = None) -> VerificationResult:
    """Re-probe the active model up to `verify_retries` times, returning as soon as
    it is recovered (giving a freshly-promoted backup time to warm up). Returns the
    last result if it never recovers within the budget."""
    s = config.settings
    attempts = max(1, s.verify_retries)
    result = _probe_once(model_name, endpoint_url, baseline)
    for _attempt in range(1, attempts):
        if result.recovered:
            return result
        time.sleep(s.verify_backoff_seconds)
        result = _probe_once(model_name, endpoint_url, baseline)
    return result
