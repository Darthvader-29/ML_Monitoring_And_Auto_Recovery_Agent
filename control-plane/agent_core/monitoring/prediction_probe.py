"""OBSERVE — send a batch of inputs to /predict and record the outcomes.

Implements the prediction-probe half of the Observe phase
(monitoring_and_metrics.md §3.2): it derives the confidence family and the
agent-observed inference_failure_rate (connection/timeouts the service never even
recorded). No labels are needed for these leading indicators.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

import config

_TIMEOUT = (config.settings.http_connect_timeout_seconds,
            config.settings.http_read_timeout_seconds)


@dataclass
class PredictionProbe:
    attempted: int
    failed: int
    avg_confidence: float
    inference_failure_rate: float
    sample_prediction: float | None = None
    sample_confidence: float | None = None


def probe_predictions(endpoint_url: str, rows: list[dict]) -> PredictionProbe:
    """POST each row to /predict; aggregate confidence + agent-observed failures."""
    attempted = failed = 0
    confidences: list[float] = []
    sample_pred = sample_conf = None

    for row in rows:
        attempted += 1
        try:
            resp = requests.post(f"{endpoint_url}/predict",
                                 json={"features": row}, timeout=_TIMEOUT)
            if resp.status_code != 200:
                failed += 1
                continue
            body = resp.json()
            conf = float(body.get("confidence", 0.0))
            confidences.append(conf)
            if sample_pred is None:
                sample_pred = float(body.get("prediction", 0))
                sample_conf = conf
        except (requests.RequestException, ValueError):
            failed += 1

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    fail_rate = failed / attempted if attempted else 0.0
    return PredictionProbe(
        attempted=attempted,
        failed=failed,
        avg_confidence=avg_conf,
        inference_failure_rate=fail_rate,
        sample_prediction=sample_pred,
        sample_confidence=sample_conf,
    )
