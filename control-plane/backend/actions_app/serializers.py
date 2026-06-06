"""DRF serializers for actions_app (api_contracts.md §B.3)."""
from __future__ import annotations

from rest_framework import serializers

from .models import ActionLog


class ActionLogSerializer(serializers.ModelSerializer):
    target_model = serializers.CharField(source="model_version.model.model_name",
                                         read_only=True)
    incident_id = serializers.IntegerField(source="incident.id", read_only=True)
    verification = serializers.SerializerMethodField()

    class Meta:
        model = ActionLog
        fields = ["id", "incident_id", "action", "severity", "target_model", "reason",
                  "decided_at", "executed_at", "outcome", "jenkins_build_id",
                  "before_metrics", "after_metrics", "is_reversible",
                  "reverted_by", "verification"]

    def get_verification(self, obj):
        v = getattr(obj, "verification", None)
        if v is None:
            return None
        return {"success": v.success, "decision": v.decision,
                "post_metrics": v.post_metrics, "verified_at": v.verified_at}
