"""DRF serializers for monitoring_app (api_contracts.md §B.1)."""
from __future__ import annotations

from rest_framework import serializers

from .models import MetricSnapshot


class MetricSnapshotSerializer(serializers.ModelSerializer):
    model_name = serializers.CharField(source="model_version.model.model_name",
                                       read_only=True)
    model_version_str = serializers.CharField(source="model_version.version",
                                              read_only=True)

    class Meta:
        model = MetricSnapshot
        fields = ["id", "model_name", "model_version_str", "timestamp",
                  "request_count", "error_count", "error_rate", "avg_latency_ms",
                  "p95_latency_ms", "avg_confidence", "accuracy", "f1",
                  "overall_drift_score", "drifted_feature_count", "health_status"]
