"""Shared FastAPI inference-service factory.

`create_app(model_name, model_version, model_path)` builds the identical service
both model_a and model_b ran as near-duplicate copies. Serves the three endpoints
the agent's OBSERVE phase relies on (api_contracts.md §A): POST /predict, GET
/health, GET /metrics, with the same response envelopes, fault-injection hooks and
metrics tracker as before.

This is a shared BASE (architecture.md §1.3 forbids importing one running service
from another, not factoring out a common base), parameterized entirely by env-set
identity, so the two services are now thin entrypoints.

Optional fault-injection hooks (read from env per request):
  FAULT_ERROR_RATE        float in [0,1]  — fraction of /predict calls that error (A1)
  FAULT_EXTRA_LATENCY_MS  float           — extra latency added per call (S1)
  FAULT_CONFIDENCE_COLLAPSE  "1"/"true"   — force confidence ~0.5 (A2)
"""
from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from _common.metrics import MetricsTracker

# ---- Feature schema (data_simulation.md §2.1) ---------------------------
# One copy shared by both services (still independent of the agent side, per the
# no-cross-service-import rule).
NUMERIC_COLS = [
    "amount", "account_age_days", "num_txn_24h",
    "avg_txn_amount", "time_since_last_min", "device_risk",
]
CATEGORICAL_COLS = ["country", "channel"]
FEATURE_COLS = NUMERIC_COLS + CATEGORICAL_COLS
ALLOWED_WINDOWS = {"1m", "5m", "15m", "all"}


# ---- Stateless helpers --------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _error(status: int, code: str, message: str, request_id: str = "",
           details=None) -> JSONResponse:
    """Standard error envelope (api_contracts.md §Conventions)."""
    return JSONResponse(
        status_code=status,
        content={"error": {
            "code": code, "message": message, "details": details,
            "request_id": request_id, "timestamp": _now_iso(),
        }},
        headers={"X-API-Version": "1"},
    )


class FeatureError(ValueError):
    """Raised when the request feature vector is missing/ill-typed."""


def _extract_features(payload: dict) -> dict:
    """Normalize either {"features": {...}} / {"features": [...]} or a flat object
    into the canonical 8-field dict (data_simulation.md §8 shows the flat form)."""
    raw = payload.get("features", payload)
    if isinstance(raw, list):
        if len(raw) != len(FEATURE_COLS):
            raise FeatureError(
                f"Expected {len(FEATURE_COLS)} features, received {len(raw)}.")
        raw = dict(zip(FEATURE_COLS, raw))
    if not isinstance(raw, dict):
        raise FeatureError("`features` must be an object or array.")

    row: dict = {}
    for col in FEATURE_COLS:
        if col not in raw or raw[col] is None:
            raise FeatureError(f"missing {col}")
        value = raw[col]
        if col in NUMERIC_COLS:
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise FeatureError(f"{col} must be numeric") from None
            # NaN/Inf pass float() but would silently poison the model — reject them.
            if not math.isfinite(value):
                raise FeatureError(f"{col} must be a finite number")
        else:
            value = str(value)
        row[col] = value
    return row


def _maybe_inject_fault() -> None:
    """Raise to simulate an inference fault (A1), per env config."""
    rate = float(os.environ.get("FAULT_ERROR_RATE", "0") or 0)
    if rate > 0 and np.random.default_rng().random() < rate:
        raise RuntimeError("injected inference fault (FAULT_ERROR_RATE)")
    extra = float(os.environ.get("FAULT_EXTRA_LATENCY_MS", "0") or 0)
    if extra > 0:
        time.sleep(extra / 1000.0)


def _fault_confidence_collapse() -> bool:
    return os.environ.get("FAULT_CONFIDENCE_COLLAPSE", "").lower() in {"1", "true", "yes"}


def _to_native(value):
    """Convert a numpy scalar to a native, JSON-serializable Python value."""
    item = getattr(value, "item", None)
    return item() if callable(item) else value


def _derive_prediction(classes, proba) -> tuple[object, float | None]:
    """Derive (prediction, score) from a model's classes_ and a proba row.

    - ``prediction`` is the actual predicted class label (argmax of ``proba``),
      converted to a native Python value but NOT forced to int — so integer
      labels stay 0/1 and string labels stay strings.
    - ``score`` is the probability of the POSITIVE class, defined as the class
      with the largest label among ``classes`` (for [0, 1] this is class 1,
      preserving the previous behavior; for string classes this is the
      lexicographically largest label, e.g. 'high' over 'low'). Only genuinely
      binary models (len(classes) == 2) get a score; otherwise it is None.
    """
    pred_idx = int(np.argmax(proba))
    prediction = _to_native(classes[pred_idx])

    score = None
    if len(classes) == 2:
        pos_idx = max(range(len(classes)), key=lambda i: classes[i])
        score = float(proba[pos_idx])
    return prediction, score


# ---- App factory --------------------------------------------------------

def create_app(model_name: str, model_version: str, model_path: Path) -> FastAPI:
    app = FastAPI(title=f"{model_name} inference service")
    metrics = MetricsTracker()
    try:
        model = joblib.load(model_path)
    except Exception:  # noqa: BLE001 — any load failure => service reports not-loaded
        model = None

    @app.post("/predict")
    async def predict(request: Request):
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return _error(400, "invalid_json", "Body is not valid JSON.")
        if not isinstance(payload, dict):
            return _error(400, "invalid_json", "Body must be a JSON object.")

        request_id = str(payload.get("request_id", ""))
        return_proba = bool(payload.get("return_proba", True))

        if model is None:
            metrics.record(0.0, float("nan"), is_error=True)
            return _error(503, "model_not_loaded", "model.pkl not loaded.", request_id)

        try:
            features = _extract_features(payload)
        except FeatureError as exc:
            metrics.record(0.0, float("nan"), is_error=True)
            return _error(422, "feature_validation_error", str(exc), request_id,
                          details={"features": str(exc)})

        start = time.perf_counter()
        try:
            _maybe_inject_fault()
            frame = pd.DataFrame([features], columns=FEATURE_COLS)
            proba = model.predict_proba(frame)[0]
            classes = list(model.classes_)
            prediction, score = _derive_prediction(classes, proba)
            confidence = 0.5 if _fault_confidence_collapse() else float(np.max(proba))
            probabilities = [float(p) for p in proba]
            latency_ms = (time.perf_counter() - start) * 1000.0
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - start) * 1000.0
            metrics.record(latency_ms, float("nan"), is_error=True)
            return _error(500, "inference_error", f"Inference failed: {exc}", request_id)

        metrics.record(latency_ms, confidence, is_error=False)
        body = {
            "prediction": prediction,
            "confidence": round(confidence, 6),
            "score": round(score, 6) if score is not None else None,
            "model_name": model_name,
            "model_version": model_version,
            "latency_ms": round(latency_ms, 3),
            "request_id": request_id,
            "timestamp": _now_iso(),
        }
        if return_proba:
            body["probabilities"] = [round(p, 6) for p in probabilities]
        return JSONResponse(content=body, headers={"X-API-Version": "1"})

    @app.get("/health")
    async def health():
        loaded = model is not None
        body = {
            "status": "healthy" if loaded else "unhealthy",
            "model_loaded": loaded,
            "model_name": model_name,
            "version": model_version if loaded else None,
            "uptime_seconds": round(metrics.uptime_seconds, 3),
            "timestamp": _now_iso(),
        }
        return JSONResponse(content=body, status_code=200 if loaded else 503,
                            headers={"X-API-Version": "1"})

    @app.get("/metrics")
    async def get_metrics(window: str = "5m"):
        if window not in ALLOWED_WINDOWS:
            return _error(400, "invalid_window",
                          f"window must be one of {sorted(ALLOWED_WINDOWS)}.")
        if model is None:
            return _error(503, "model_not_loaded", "Metrics unavailable; model not loaded.")
        snap = metrics.snapshot(window)
        snap.update({
            "model_name": model_name,
            "model_version": model_version,
            "timestamp": _now_iso(),
        })
        return JSONResponse(content=snap, headers={"X-API-Version": "1"})

    return app
