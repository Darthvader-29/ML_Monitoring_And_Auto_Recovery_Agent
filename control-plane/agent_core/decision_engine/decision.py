"""DECIDE — assemble the immutable Decision for a tick.

Glues severity_classifier + policy_rules into a single Decision object
(api_contracts.md §D), choosing the failover target and carrying the worst
detection signal as evidence.
"""
from __future__ import annotations

from typing import Optional

from schemas import SEVERITY_RANK, ActionType, Decision, DetectionResult

from decision_engine import policy_rules, severity_classifier


def _worst(detections: list[DetectionResult]) -> Optional[DetectionResult]:
    # Only consider signals that actually breached. Detectors emit non-breaching
    # rows too (e.g. the clean data_drift_aggregate with anomaly_detected=False);
    # without this filter one of those could be attached as the Decision's
    # detection_signal, misrepresenting why the agent acted.
    breaching = [d for d in detections if d.anomaly_detected]
    if not breaching:
        return None
    return max(breaching,
               key=lambda d: SEVERITY_RANK[severity_classifier.classify_detection(d)])


def make_decision(
    detections: list[DetectionResult],
    *,
    active_model: str,
    backup_model: str,
    backup_healthy: bool,
    action_gated: bool,
) -> Decision:
    severity = severity_classifier.classify(detections)
    action, rationale = policy_rules.choose_action(
        severity, backup_healthy=backup_healthy, action_gated=action_gated)

    worst = _worst(detections)
    # Switch targets the backup; everything else concerns the active model.
    target = backup_model if action == ActionType.SWITCH_BACKUP else active_model
    reason = rationale if worst is None else f"{rationale} [{worst.message}]"

    return Decision(
        action=action,
        severity=severity,
        target_model=target,
        reason=reason,
        detection_signal=worst,
        requires_jenkins=False,   # MVP uses the direct executor; Jenkins arrives Phase 5
    )
