"""DRF serializers for registry_app (api_contracts.md §B.2 wire shape)."""
from __future__ import annotations

from rest_framework import serializers

from .models import ModelVersion


class ModelVersionSerializer(serializers.ModelSerializer):
    model_name = serializers.CharField(source="model.model_name", read_only=True)
    active_flag = serializers.BooleanField(source="is_active", read_only=True)

    class Meta:
        model = ModelVersion
        fields = ["id", "model_name", "version", "active_flag", "status",
                  "endpoint_url", "port", "metrics_at_training", "created_at"]


class ActiveModelSerializer(serializers.Serializer):
    """The resolved active model (GET /api/active-model)."""
    model_name = serializers.CharField()
    version = serializers.CharField()
    active_flag = serializers.BooleanField()
    status = serializers.CharField()
    endpoint_url = serializers.CharField()
    port = serializers.IntegerField(allow_null=True)
    switched_at = serializers.DateTimeField(required=False)
    switched_by = serializers.CharField(required=False)
