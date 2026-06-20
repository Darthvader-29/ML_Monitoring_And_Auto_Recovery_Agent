"""Shared helpers for the API views.

Consolidates three things that were copy-pasted (and drifting) across the apps:
the `{"error": {...}}` envelope, the ingestion `resolve_version` (two divergent
copies in monitoring_app/actions_app), and the `?limit` parser.
"""
from __future__ import annotations

from typing import Optional

from rest_framework import status
from rest_framework.response import Response

_DEFAULT_LIMIT, _MAX_LIMIT = 50, 500


def error_response(code: str, message: Optional[str] = None,
                   status_code: int = status.HTTP_400_BAD_REQUEST) -> Response:
    """The single error envelope shape used by every endpoint."""
    body = {"error": {"code": code}}
    if message is not None:
        body["error"]["message"] = message
    return Response(body, status=status_code)


def parse_limit(request) -> int:
    """A safe `?limit`: non-integer/negative falls back to the default, and the value
    is capped so a caller cannot pull the whole table (or crash a negative slice)."""
    raw = request.query_params.get("limit", _DEFAULT_LIMIT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    return min(n, _MAX_LIMIT) if n > 0 else _DEFAULT_LIMIT


def resolve_version(model_name: str, version: Optional[str] = None):
    """Resolve (creating if needed) the ModelVersion an ingestion call targets.

    Normalizes the name so a stray-whitespace typo ("model_a " vs "model_a") does
    NOT spawn a phantom row. With `version` given, resolves that exact version; with
    none, the latest version (creating an 'unknown' row if the model has none yet).
    """
    from registry_app.models import Model, ModelVersion
    model_name = str(model_name).strip()
    model, _ = Model.objects.get_or_create(model_name=model_name)
    if version:
        mv, _ = ModelVersion.objects.get_or_create(
            model=model, version=str(version),
            defaults={"artifact_path": f"model-services/{model_name}/model.pkl"})
        return mv
    mv = model.versions.order_by("-created_at").first()
    if mv is None:
        mv = ModelVersion.objects.create(
            model=model, version="unknown",
            artifact_path=f"model-services/{model_name}/model.pkl")
    return mv
