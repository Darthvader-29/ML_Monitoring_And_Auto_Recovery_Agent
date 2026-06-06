"""Client for triggering Jenkins recovery jobs.

Not implemented until Phase 5 — the MVP and Phases 2-4 use the direct executor in
actions/switch_model.py. When implemented, this triggers parameterized builds via
buildWithParameters and polls build status (api_contracts.md §C,
deployment_and_devops.md §6.6). Defined here so the executor interface is stable.
"""
from __future__ import annotations


class JenkinsClient:  # pragma: no cover - Phase 5
    def trigger_job(self, job_name: str, params: dict):
        raise NotImplementedError("Jenkins executor lands in Phase 5")

    def poll_build(self, queue_url: str):
        raise NotImplementedError("Jenkins executor lands in Phase 5")
