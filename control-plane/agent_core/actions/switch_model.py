"""ACT — switch active traffic to the backup (or fail closed).

The executor is selected by config (failure_scenarios.md §1.4, roadmap Phase 2/5):
  - "direct"  (MVP): flip the active model in-memory (and, once Phase 4 lands, via
                     the Django registry through django_client).
  - "jenkins" (Phase 5): same interface, recovery runs through a Jenkins job.
Only the "direct" executor exists today; the Jenkins branch is wired in Phase 5.

Also implements DISABLE_PREDICTIONS (degrade / fail-closed) for the S4 escalation
path where no healthy backup exists.
"""
from __future__ import annotations

import logging

import config
from schemas import ActionResult, ActionType, Decision, Outcome

log = logging.getLogger("agent.actions")


def execute(decision: Decision, runtime, django_client=None) -> ActionResult:
    if decision.action == ActionType.DISABLE_PREDICTIONS:
        runtime.serving_enabled = False
        log.error("DISABLE predictions (fail closed): %s", decision.reason)
        return ActionResult(
            action=decision.action, target_model=decision.target_model,
            executed=True, outcome=Outcome.SUCCESS, message="serving disabled (degrade mode)",
        )

    previous = runtime.active_model
    target = decision.target_model
    if target == previous:
        return ActionResult(
            action=decision.action, target_model=target,
            executed=False, outcome=Outcome.SKIPPED, message="target already active",
        )

    if config.settings.executor_type == "jenkins":
        # Phase 5 wires clients/jenkins_client.py here behind this same interface.
        log.warning("jenkins executor not yet implemented; falling back to direct flip")

    # Direct executor: flip the in-memory active pointer (the source of truth until
    # Phase 4 persists it in the Django registry).
    runtime.previous_model = previous
    runtime.active_model = target
    if django_client is not None:
        django_client.set_active_model(target, reason=decision.reason)

    log.warning("SWITCH active %s -> %s (%s)", previous, target, decision.reason)
    return ActionResult(
        action=ActionType.SWITCH_BACKUP, target_model=target,
        executed=True, outcome=Outcome.PENDING,
        message=f"switched {previous} -> {target}",
    )
