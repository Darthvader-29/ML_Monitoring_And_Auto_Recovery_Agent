"""ACT — pluggable recovery executors (strategy + factory).

The recovery backend (in-memory "direct" flip vs a Jenkins job) used to be an
inline `if config.settings.executor_type == "jenkins"` branch inside
`switch_model.execute`. This module turns that into a strategy: a single
`Executor.switch(target, reason) -> ExecutorResult` contract with `DirectExecutor`
and `JenkinsExecutor` implementations, selected by `make_executor(settings)` — the
one place that knows the concrete classes. Adding a backend (e.g. k8s) is a new
class + one map entry, not an edit to the action.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import config


@dataclass
class ExecutorResult:
    ok: bool
    build_number: Optional[int] = None
    build_url: Optional[str] = None
    message: str = ""


class Executor(Protocol):
    def switch(self, target: str, reason: str) -> ExecutorResult: ...


class DirectExecutor:
    """MVP backend: the in-memory active-pointer flip is performed by the caller;
    `switch` is a no-op that always reports success."""

    def switch(self, target: str, reason: str) -> ExecutorResult:
        return ExecutorResult(ok=True, message=f"direct switch to {target}")


class JenkinsExecutor:
    """Run the recovery through the parameterized Jenkins switch job. On failure we
    report ok=False so the caller does NOT flip, leaving the engine to escalate
    rather than record a half-switch."""

    def switch(self, target: str, reason: str) -> ExecutorResult:
        from clients.jenkins_client import JenkinsClient
        job = config.settings.jenkins_job_switch
        result = JenkinsClient().run_job(
            job, {"TARGET_MODEL": target, "ACTION": "switch", "REASON": reason})
        return ExecutorResult(ok=result.success, build_number=result.build_number,
                              build_url=result.build_url, message=result.message)


_EXECUTORS = {"direct": DirectExecutor, "jenkins": JenkinsExecutor}


def make_executor(settings=None) -> Executor:
    """Return the executor selected by `settings.executor_type` (default: direct)."""
    settings = settings or config.settings
    return _EXECUTORS.get(settings.executor_type, DirectExecutor)()
