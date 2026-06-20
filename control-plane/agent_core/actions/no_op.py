"""ACT — no-op: record an observation, take no recovery action.

The safe default (failure_scenarios.md §1.4). Even a no-op is auditable so a
reviewer can see the agent saw the tick and chose not to act (safety invariant §5.3).
"""
from __future__ import annotations

import logging

from schemas import ActionResult, Decision, Outcome

log = logging.getLogger("agent.actions")


def execute(decision: Decision, runtime=None, django_client=None) -> ActionResult:
    # Uniform handler signature (runtime/django_client unused here) so every action
    # is dispatchable through a single registry — see actions/dispatch.py.
    log.info("no-op: %s", decision.reason)
    return ActionResult(
        action=decision.action, target_model=decision.target_model,
        executed=True, outcome=Outcome.SUCCESS, message=decision.reason,
    )
