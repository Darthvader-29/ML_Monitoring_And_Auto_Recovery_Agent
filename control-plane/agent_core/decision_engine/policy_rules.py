"""DECIDE — map (severity, context) to a recovery action.

The safe-by-default policy (agent_logic.md, failure_scenarios.md §5.2):
  HIGH + healthy backup        -> switch traffic to backup
  HIGH + NO healthy backup     -> disable predictions (fail closed) + escalate (S4)
  HIGH but action gated        -> alert only (unconfirmed or in cooldown — anti-flap)
  MEDIUM                       -> alert
  LOW / nothing                -> no-op (monitor more)

`action_gated` folds in the CONFIRM_N persistence and COOLDOWN checks the agent
runtime tracks, so a destructive switch only fires on a confirmed, non-cooldown
HIGH (failure_scenarios.md §1.3, N1).
"""
from __future__ import annotations

from schemas import ActionType, Severity


def choose_action(
    severity: Severity,
    *,
    backup_healthy: bool,
    action_gated: bool,
) -> tuple[ActionType, str]:
    """Return (action, rationale)."""
    if severity == Severity.HIGH:
        if action_gated:
            return ActionType.ALERT, "HIGH but action gated (unconfirmed/cooldown)"
        if backup_healthy:
            return ActionType.SWITCH_BACKUP, "HIGH severity; healthy backup available"
        return (ActionType.DISABLE_PREDICTIONS,
                "HIGH severity but backup unhealthy — fail closed + escalate")
    if severity == Severity.MEDIUM:
        return ActionType.ALERT, "MEDIUM severity; alert and keep watching"
    return ActionType.NO_OP, "no confirmed actionable degradation"
