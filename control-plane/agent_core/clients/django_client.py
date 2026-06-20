"""Client for the Django control plane (metrics / registry / audit).

Phase 4: a real `requests`-based client hitting /api/metrics, /api/active-model and
/api/actions (api_contracts.md §B). Every call is resilient — a backend error is
logged and swallowed so the control loop never crashes on a persistence hiccup
(the agent's in-memory state remains the live source of truth).

get_client() probes the backend once: if reachable it returns the real client,
otherwise the NullDjangoClient (so the loop still runs with stdout-only audit, as
in Phases 2-3).
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Protocol

import requests

import config
from schemas import ActionResult, Decision, MetricSnapshot, Outcome, VerificationResult

log = logging.getLogger("agent.clients.django")


class DjangoClientProtocol(Protocol):
    """The control-plane client contract the agent loop depends on. Both
    `DjangoClient` and `NullDjangoClient` satisfy it, and `run()` accepts any
    implementation (so tests can inject a fake — see test_loop_scenarios)."""

    def post_metrics(self, snapshot: MetricSnapshot, **kw) -> None: ...
    def get_active_model(self) -> Optional[str]: ...
    def set_active_model(self, model_name: str, reason: str = "") -> None: ...
    def post_action(self, decision: Decision, result: ActionResult) -> Optional[int]: ...
    def patch_action(self, action_id: Optional[int],
                     verification: VerificationResult) -> None: ...


class NullDjangoClient:
    """No-op implementation used when no backend is reachable."""

    enabled = False

    def post_metrics(self, snapshot: MetricSnapshot, **_kw) -> None:
        log.debug("post_metrics (null): %s err=%.3f", snapshot.model_name, snapshot.error_rate)

    def get_active_model(self) -> Optional[str]:
        return None

    def set_active_model(self, model_name: str, reason: str = "") -> None:
        log.info("set_active_model (null): %s (%s)", model_name, reason)

    def post_action(self, decision: Decision, result: ActionResult) -> Optional[int]:
        return None

    def patch_action(self, action_id: Optional[int], verification: VerificationResult) -> None:
        pass


class DjangoClient:
    """Real HTTP client to the Django control plane."""

    enabled = True

    def __init__(self, base_url: str, token: str = "") -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Content-Type": "application/json"}
        if token:
            self._headers["Authorization"] = f"Token {token}"

    def _post(self, path: str, body: dict) -> Optional[dict]:
        try:
            r = requests.post(f"{self._base}{path}", json=body,
                              headers=self._headers, timeout=config.settings.http_timeout())
            if r.status_code < 300:
                return r.json()
            log.warning("POST %s -> %s", path, r.status_code)
        except (requests.RequestException, ValueError) as exc:
            log.warning("POST %s failed: %s", path, exc)
        return None

    def post_metrics(self, snapshot: MetricSnapshot, *, overall_drift_score: float = 0.0,
                     drifted_feature_count: int = 0) -> None:
        self._post("/api/metrics", {
            "model_name": snapshot.model_name,
            "model_version": snapshot.model_version,
            "request_count": snapshot.request_count,
            "error_count": snapshot.error_count,
            "error_rate": snapshot.error_rate,
            "avg_latency_ms": snapshot.avg_latency_ms,
            "p95_latency_ms": snapshot.p95_latency_ms,
            "avg_confidence": snapshot.avg_confidence,
            "accuracy": snapshot.accuracy,
            "status": snapshot.status.value,
            "overall_drift_score": overall_drift_score,
            "drifted_feature_count": drifted_feature_count,
            "timestamp": snapshot.timestamp.isoformat(),
        })

    def get_active_model(self) -> Optional[str]:
        # Idempotent GET: up to 3 attempts with exponential backoff
        # (0.25s, 0.5s) per api_contracts.md §Timeouts.
        for attempt in range(3):
            try:
                r = requests.get(f"{self._base}/api/active-model",
                                 headers=self._headers, timeout=config.settings.http_timeout())
                if r.status_code == 200:
                    return r.json().get("model_name")
            except (requests.RequestException, ValueError) as exc:
                log.warning("get_active_model attempt %d failed: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(0.25 * (2 ** attempt))
        return None

    def set_active_model(self, model_name: str, reason: str = "") -> None:
        self._post("/api/active-model", {"model_name": model_name, "reason": reason})

    def post_action(self, decision: Decision, result: ActionResult) -> Optional[int]:
        sig = (decision.detection_signal.model_dump(mode="json")
               if decision.detection_signal else None)
        body = self._post("/api/actions", {
            "action": decision.action.value, "severity": decision.severity.value,
            "target_model": decision.target_model, "reason": decision.reason,
            "outcome": result.outcome.value, "detection_signal": sig,
        })
        return body.get("id") if body else None

    def patch_action(self, action_id: Optional[int],
                     verification: VerificationResult) -> None:
        if action_id is None:
            return
        outcome = Outcome.SUCCESS if verification.recovered else Outcome.FAILED
        try:
            requests.patch(f"{self._base}/api/actions/{action_id}",
                           json={"outcome": outcome.value,
                                 "verification": verification.model_dump(mode="json")},
                           headers=self._headers, timeout=config.settings.http_timeout())
        except requests.RequestException as exc:
            log.warning("patch_action failed: %s", exc)


def get_client():
    """Return the real client if the backend answers, else the null client."""
    base = config.settings.backend_url
    try:
        r = requests.get(f"{base.rstrip('/')}/api/health/", timeout=(1.0, 1.5))
        if r.status_code == 200:
            log.info("Django backend reachable at %s — persistence enabled", base)
            return DjangoClient(base, config.settings.django_api_token)
    except requests.RequestException:
        pass
    log.info("Django backend not reachable — running with stdout-only audit")
    return NullDjangoClient()
