"""registry_app views — /api/active-model and /api/models (api_contracts.md §B.2)."""
from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from api_common import error_response
from .models import ActiveModelPointer, ModelVersion
from .serializers import ModelVersionSerializer

log = logging.getLogger(__name__)


def _active_payload(pointer: ActiveModelPointer) -> dict:
    v = pointer.model_version
    return {
        "model_name": v.model.model_name, "version": v.version,
        "active_flag": v.is_active, "status": v.status,
        "endpoint_url": v.endpoint_url, "port": v.port,
        "switched_at": pointer.switched_at, "switched_by": pointer.switched_by,
    }


class ActiveModelView(APIView):
    def get(self, _request):
        pointer = (ActiveModelPointer.objects
                   .select_related("model_version__model").filter(pk=1).first())
        if pointer is None:
            return error_response("not_found", "no active model configured",
                                  status.HTTP_404_NOT_FOUND)
        return Response(_active_payload(pointer))

    def post(self, request):
        """Flip the active model. Body: {model_name, version?, reason?, switched_by?}.
        If `version` is omitted, the most recent version of `model_name` is used."""
        data = request.data or {}
        model_name = data.get("model_name")
        if not model_name:
            return error_response("validation_error", "model_name is required")
        reason = data.get("reason", "")
        qs = ModelVersion.objects.filter(model__model_name=model_name)
        if data.get("version"):
            qs = qs.filter(version=data["version"])
        else:
            # Auto-selection must never promote a retired version.
            qs = qs.exclude(status__in=["DEPRECATED", "ROLLED_BACK"])
        version = qs.order_by("-created_at").first()
        if version is None:
            return error_response("model_not_found", f"no version for {model_name}",
                                  status.HTTP_404_NOT_FOUND)
        switched_by = data.get("switched_by", "agent")
        log.info("active-model switch model=%s version=%s by=%s reason=%s",
                 model_name, version.version, switched_by, reason)
        pointer = ActiveModelPointer.switch_to(version, by=switched_by)
        payload = _active_payload(pointer)
        payload["reason"] = reason
        return Response(payload)

    put = post  # idempotent upsert (api_contracts.md §B.2)


class ModelListView(ListAPIView):
    serializer_class = ModelVersionSerializer
    pagination_class = None

    def get_queryset(self):
        qs = ModelVersion.objects.select_related("model").all()
        model = self.request.query_params.get("model")
        return qs.filter(model__model_name=model) if model else qs
