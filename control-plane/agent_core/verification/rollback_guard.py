"""VERIFY — undo a recovery that did not verify, then escalate.

If the VERIFY phase reports the action did not recover the system, revert to the
previous known-good state and (since MAX_RECOVERY_ATTEMPTS is reached) escalate to
a human rather than retrying forever (failure_scenarios.md R1, safety invariant §6).
Returns the rollback ActionResult, or None if no rollback was needed.
"""
from __future__ import annotations

import logging
from typing import Optional

from schemas import ActionResult, ActionType, Outcome, VerificationResult

log = logging.getLogger("agent.verification")


def guard(verification: VerificationResult, runtime, django_client=None) -> Optional[ActionResult]:
    if verification.recovered:
        return None

    previous = getattr(runtime, "previous_model", None)
    if not previous or previous == runtime.active_model:
        # Nothing to roll back to (e.g. a degrade/disable). Escalate only.
        verification.escalate_to_human = True
        log.error("recovery failed and no rollback target — escalating to human")
        return ActionResult(
            action=ActionType.ALERT, target_model=runtime.active_model,
            executed=True, outcome=Outcome.FAILED,
            message="recovery failed; no rollback target; escalated",
        )

    failed_target = runtime.active_model
    runtime.active_model = previous
    runtime.previous_model = None
    verification.escalate_to_human = True
    if django_client is not None:
        django_client.set_active_model(previous, reason="rollback: recovery failed")

    log.error("ROLLBACK %s -> %s (recovery failed); escalating to human",
              failed_target, previous)
    return ActionResult(
        action=ActionType.ROLLBACK, target_model=previous,
        executed=True, outcome=Outcome.SUCCESS,
        message=f"rolled back {failed_target} -> {previous}; escalated",
    )
