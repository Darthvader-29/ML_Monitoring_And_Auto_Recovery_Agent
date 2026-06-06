"""monitoring_app views — POST/GET /api/metrics, /api/metrics/latest."""
from __future__ import annotations

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from registry_app.models import Model, ModelVersion

from .models import MetricSnapshot
from .serializers import MetricSnapshotSerializer

# agent health string (healthy/degraded/unhealthy) -> DB enum
_HEALTH_MAP = {"healthy": "HEALTHY", "degraded": "DEGRADED", "unhealthy": "UNHEALTHY"}


def _resolve_version(model_name: str, version: str) -> ModelVersion:
    model, _ = Model.objects.get_or_create(model_name=model_name)
    mv, _ = ModelVersion.objects.get_or_create(
        model=model, version=version or "unknown",
        defaults={"artifact_path": f"model-services/{model_name}/model.pkl"})
    return mv


class MetricsView(APIView):
    def post(self, request):
        d = request.data or {}
        model_name = d.get("model_name")
        if not model_name:
            return Response({"error": {"code": "validation_error",
                             "message": "model_name required"}},
                            status=status.HTTP_400_BAD_REQUEST)
        mv = _resolve_version(model_name, str(d.get("model_version", "unknown")))
        snap = MetricSnapshot.objects.create(
            model_version=mv,
            timestamp=d.get("timestamp") or timezone.now(),
            request_count=int(d.get("request_count", 0)),
            error_count=int(d.get("error_count", 0)),
            error_rate=float(d.get("error_rate", 0.0)),
            avg_latency_ms=float(d.get("avg_latency_ms", 0.0)),
            p95_latency_ms=float(d.get("p95_latency_ms", 0.0)),
            avg_confidence=d.get("avg_confidence"),
            accuracy=d.get("accuracy"),
            overall_drift_score=float(d.get("overall_drift_score", 0.0)),
            drifted_feature_count=int(d.get("drifted_feature_count", 0)),
            health_status=_HEALTH_MAP.get(str(d.get("status", "healthy")).lower(),
                                          "UNKNOWN"),
            raw=d if isinstance(d, dict) else {},
        )
        return Response(MetricSnapshotSerializer(snap).data,
                        status=status.HTTP_201_CREATED)

    def get(self, request):
        qs = MetricSnapshot.objects.select_related("model_version__model").all()
        model = request.query_params.get("model")
        if model:
            qs = qs.filter(model_version__model__model_name=model)
        qs = qs[:int(request.query_params.get("limit", 50))]
        return Response(MetricSnapshotSerializer(qs, many=True).data)


class LatestMetricsView(APIView):
    def get(self, request):
        out = {}
        for mv in ModelVersion.objects.select_related("model").all():
            snap = mv.snapshots.first()
            if snap:
                out[mv.model.model_name] = MetricSnapshotSerializer(snap).data
        return Response(out)
