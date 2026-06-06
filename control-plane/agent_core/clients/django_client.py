"""Client for the Django control plane (metrics / registry / audit).

Phase 2 ships a NULL client: it satisfies the interface the loop calls but only
logs — there is no Django backend yet (MVP keeps state in-memory and audits to
stdout). Phase 4 replaces NullDjangoClient with a real `requests`-based client
hitting /api/metrics, /api/active-model and /api/actions (api_contracts.md §B).

`get_client()` is the single construction point so the loop is agnostic to which
implementation is active.
"""
from __future__ import annotations

import logging
from typing import Optional

from schemas import ActionResult, Decision, MetricSnapshot, VerificationResult

log = logging.getLogger("agent.clients.django")


class NullDjangoClient:
    """No-op implementation used until the Django backend exists (Phase 4)."""

    enabled = False

    def post_metrics(self, snapshot: MetricSnapshot) -> None:
        log.debug("post_metrics (null): %s err=%.3f", snapshot.model_name,
                  snapshot.error_rate)

    def get_active_model(self) -> Optional[str]:
        return None  # MVP: the agent's in-memory pointer is the source of truth

    def set_active_model(self, model_name: str, reason: str = "") -> None:
        log.info("set_active_model (null): %s (%s)", model_name, reason)

    def post_action(self, decision: Decision, result: ActionResult) -> Optional[int]:
        log.debug("post_action (null): %s -> %s", decision.action.value, result.outcome.value)
        return None

    def patch_action(self, action_id: Optional[int],
                     verification: VerificationResult) -> None:
        log.debug("patch_action (null): recovered=%s", verification.recovered)


def get_client():
    """Return the active Django client. Phase 4 will return the real HTTP client
    when a backend URL/token is configured."""
    return NullDjangoClient()
