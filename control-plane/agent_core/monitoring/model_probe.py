"""OBSERVE — probe a model service's /health and /metrics.

Implements the model-probe half of the Observe phase (monitoring_and_metrics.md
§3.2): it reads system signals the service self-tracks and folds them into a
MetricSnapshot. A timeout / connection error is itself an observation
(reachable=False, status UNHEALTHY) rather than a crash (api_contracts.md
§Conventions: "probe failure is a signal").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

import config
from schemas import HealthStatus, MetricSnapshot

_TIMEOUT = (config.settings.http_connect_timeout_seconds,
            config.settings.http_read_timeout_seconds)
_HEALTH_TIMEOUT = (config.settings.http_connect_timeout_seconds, 1.5)


@dataclass
class HealthProbe:
    reachable: bool
    status: HealthStatus
    model_loaded: bool
    version: Optional[str] = None
    uptime_seconds: Optional[float] = None


def probe_health(endpoint_url: str) -> HealthProbe:
    """GET /health. Unreachable or non-2xx => UNHEALTHY (still a valid observation)."""
    try:
        resp = requests.get(f"{endpoint_url}/health", timeout=_HEALTH_TIMEOUT)
        body = resp.json()
        status = HealthStatus(body.get("status", "unhealthy"))
        return HealthProbe(
            reachable=True,
            status=status,
            model_loaded=bool(body.get("model_loaded", False)),
            version=body.get("version"),
            uptime_seconds=body.get("uptime_seconds"),
        )
    except (requests.RequestException, ValueError, KeyError):
        return HealthProbe(reachable=False, status=HealthStatus.UNHEALTHY,
                           model_loaded=False)


def probe_metrics(endpoint_url: str, model_name: str) -> Optional[MetricSnapshot]:
    """GET /metrics -> MetricSnapshot. Returns None if the service is unreachable."""
    try:
        resp = requests.get(f"{endpoint_url}/metrics", params={"window": "5m"},
                            timeout=_TIMEOUT)
        if resp.status_code != 200:
            return None
        m = resp.json()
    except (requests.RequestException, ValueError):
        return None

    return MetricSnapshot(
        model_name=m.get("model_name", model_name),
        model_version=str(m.get("model_version", "unknown")),
        request_count=int(m.get("request_count", 0)),
        error_count=int(m.get("error_count", 0)),
        error_rate=float(m.get("error_rate", 0.0)),
        avg_latency_ms=float(m.get("avg_latency_ms", 0.0)),
        p95_latency_ms=float(m.get("p95_latency_ms", 0.0)),
        avg_confidence=float(m.get("avg_confidence", 0.0)),
    )
