"""monitoring_app views — POST/GET /api/metrics, /api/metrics/latest."""
from __future__ import annotations

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from registry_app.models import Model, ModelVersion

from .models import MetricSnapshot
from .serializers import MetricSnapshotSerializer


def _parse_timestamp(value):
    """Parse a client-supplied timestamp safely. A missing or unparseable value
    falls back to now(); a naive datetime is made timezone-aware (USE_TZ=True) so
    ordering by -timestamp stays well-defined and a bad client string cannot 500
    the ingest or make a stale row sort as 'latest'."""
    if not value:
        return timezone.now()
    parsed = parse_datetime(value) if isinstance(value, str) else value
    if parsed is None:
        return timezone.now()
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed

# agent health string (healthy/degraded/unhealthy) -> DB enum
_HEALTH_MAP = {"healthy": "HEALTHY", "degraded": "DEGRADED", "unhealthy": "UNHEALTHY"}

_DEFAULT_LIMIT, _MAX_LIMIT = 50, 500


def _parse_limit(request) -> int:
    """A safe `?limit`: non-integer/negative falls back to the default, capped so a
    caller cannot pull the whole table or crash the view with a negative slice."""
    raw = request.query_params.get("limit", _DEFAULT_LIMIT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    return min(n, _MAX_LIMIT) if n > 0 else _DEFAULT_LIMIT


def _resolve_version(model_name: str, version: str) -> ModelVersion:
    # Normalize so a stray-whitespace typo resolves to the SAME registry row instead
    # of materializing a phantom Model/ModelVersion.
    model_name = str(model_name).strip()
    model, _ = Model.objects.get_or_create(model_name=model_name)
    mv, _ = ModelVersion.objects.get_or_create(
        model=model, version=version or "unknown",
        defaults={"artifact_path": f"model-services/{model_name}/model.pkl"})
    return mv


class MetricsView(APIView):
    def post(self, request):
        d = request.data or {}
        model_name = str(d.get("model_name") or "").strip()
        if not model_name:
            return Response({"error": {"code": "validation_error",
                             "message": "model_name required"}},
                            status=status.HTTP_400_BAD_REQUEST)
        mv = _resolve_version(model_name, str(d.get("model_version", "unknown")))
        snap = MetricSnapshot.objects.create(
            model_version=mv,
            timestamp=_parse_timestamp(d.get("timestamp")),
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
        qs = qs[:_parse_limit(request)]
        return Response(MetricSnapshotSerializer(qs, many=True).data)


class LatestMetricsView(APIView):
    def get(self, request):
        out = {}
        for mv in ModelVersion.objects.select_related("model").all():
            snap = mv.snapshots.first()
            if snap:
                out[mv.model.model_name] = MetricSnapshotSerializer(snap).data
        return Response(out)
