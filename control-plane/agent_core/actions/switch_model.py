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

    build_number = build_url = None
    if config.settings.executor_type == "jenkins":
        # Jenkins executor (Phase 5): delegate the mutation to the recovery job.
        # Same interface as direct; on Jenkins failure we do NOT flip and report it,
        # so the decision engine can escalate rather than leave a half-switch.
        ok, build_number, build_url = _run_via_jenkins(target, decision.reason)
        if not ok:
            return ActionResult(
                action=ActionType.SWITCH_BACKUP, target_model=target,
                executed=False, outcome=Outcome.FAILED,
                jenkins_build_number=build_number, jenkins_build_url=build_url,
                message=f"jenkins switch to {target} failed",
            )

    # Flip the in-memory active pointer and mirror it into the Django registry.
    runtime.previous_model = previous
    runtime.active_model = target
    if django_client is not None:
        django_client.set_active_model(target, reason=decision.reason)

    log.warning("SWITCH active %s -> %s (%s)", previous, target, decision.reason)
    return ActionResult(
        action=ActionType.SWITCH_BACKUP, target_model=target,
        executed=True, outcome=Outcome.PENDING,
        jenkins_build_number=build_number, jenkins_build_url=build_url,
        message=f"switched {previous} -> {target}",
    )


def _run_via_jenkins(target: str, reason: str):
    """Trigger the switch_active_model Jenkins job; return (ok, build_no, build_url)."""
    from clients.jenkins_client import JenkinsClient
    job = config.settings.jenkins_job_switch
    result = JenkinsClient().run_job(job, {
        "TARGET_MODEL": target, "ACTION": "switch", "REASON": reason})
    log.warning("jenkins %s: %s", job, result.message)
    return result.success, result.build_number, result.build_url
