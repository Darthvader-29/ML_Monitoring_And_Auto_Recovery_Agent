"""ACT — alert-only: surface a condition without changing traffic.

Used for MEDIUM severity, and for a HIGH that is gated by the anti-flap rules
(failure_scenarios.md §1.4). Informational and side-effect-free beyond emitting
the alert.
"""
from __future__ import annotations

import logging

from schemas import ActionResult, Decision, Outcome

log = logging.getLogger("agent.actions")


def execute(decision: Decision, runtime=None, django_client=None) -> ActionResult:
    # Uniform handler signature (runtime/django_client unused here) so every action
    # is dispatchable through a single registry — see actions/dispatch.py.
    log.warning("ALERT [%s] %s", decision.severity.value, decision.reason)
    return ActionResult(
        action=decision.action, target_model=decision.target_model,
        executed=True, outcome=Outcome.SUCCESS, message="alert emitted",
    )
