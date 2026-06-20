"""ACT — action dispatch registry.

Replaces the `if/elif` over `ActionType` in the agent loop with a table mapping
each action to a handler. Every handler shares the signature
`execute(decision, runtime, django_client) -> ActionResult`, so adding a
first-class action (e.g. RETRAIN, ROLLBACK) is a registry entry, not an edit to
`run_tick`. Mirrors the Django side's `_ACTION_MAP` table.
"""
from __future__ import annotations

from schemas import ActionResult, ActionType, Decision

from actions import alert, no_op, switch_model

ACTION_HANDLERS = {
    ActionType.SWITCH_BACKUP: switch_model.execute,
    ActionType.DISABLE_PREDICTIONS: switch_model.execute,
    ActionType.ALERT: alert.execute,
    ActionType.NO_OP: no_op.execute,
}


def dispatch(decision: Decision, runtime, django_client=None) -> ActionResult:
    """Run the handler registered for `decision.action` (no-op for any unmapped
    action — safe by default)."""
    handler = ACTION_HANDLERS.get(decision.action, no_op.execute)
    return handler(decision, runtime, django_client)
