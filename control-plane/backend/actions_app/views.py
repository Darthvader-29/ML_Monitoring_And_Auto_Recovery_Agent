"""actions_app views — POST/GET /api/actions, PATCH /api/actions/{id}.

Translates the agent's flat action vocabulary (api_contracts.md §B.3) into the
normalized Incident/ActionLog/VerificationResult rows (data_model.md §5).
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from registry_app.models import Model, ModelVersion

from .models import ActionLog, Incident, VerificationResult
from .serializers import ActionLogSerializer

# agent ActionType (lowercase) -> DB ACTION enum
_ACTION_MAP = {
    "no_op": "NO_OP", "alert": "ALERT", "switch_backup": "SWITCH",
    "rollback": "ROLLBACK", "retrain": "RETRAIN", "disable_predictions": "DISABLE",
}
# A skipped action did NOT succeed (e.g. target already active) — it must map to
# its own SKIPPED outcome, not be silently recorded as SUCCESS in the audit trail.
_OUTCOME_MAP = {"pending": "PENDING", "success": "SUCCESS", "failed": "FAILED",
                "skipped": "SKIPPED", "reverted": "REVERTED"}
_NONTRIVIAL = {"SWITCH", "ROLLBACK", "RETRAIN", "DISABLE"}
# Actions the agent can automatically undo: a traffic SWITCH can be switched back,
# a ROLLBACK re-applied, DISABLE re-enabled. NO_OP/ALERT have nothing to revert and
# RETRAIN is not cleanly reversible. (The old `action in _NONTRIVIAL or action in
# ("NO_OP","ALERT")` covered every action, so the flag was always True.)
_REVERSIBLE = {"SWITCH", "ROLLBACK", "DISABLE"}


def _resolve_version(model_name: str) -> ModelVersion:
    model, _ = Model.objects.get_or_create(model_name=model_name)
    mv = model.versions.order_by("-created_at").first()
    if mv is None:
        mv = ModelVersion.objects.create(
            model=model, version="unknown",
            artifact_path=f"model-services/{model_name}/model.pkl")
    return mv


def _incident_for(version: ModelVersion, severity: str) -> Incident:
    """Reuse the latest non-terminal incident for this version, else open one."""
    inc = (version.incidents
           .exclude(status__in=["RESOLVED", "ESCALATED"]).order_by("-opened_at").first())
    if inc is None:
        inc = Incident.objects.create(affected_version=version, severity=severity)
    return inc


class ActionsView(APIView):
    def post(self, request):
        d = request.data or {}
        action = _ACTION_MAP.get(str(d.get("action", "no_op")), "NO_OP")
        target = d.get("target_model") or "model_a"
        version = _resolve_version(target)
        severity = str(d.get("severity", "LOW")).upper()
        incident = _incident_for(version, severity)

        log = ActionLog.objects.create(
            incident=incident, model_version=version, action=action,
            severity=severity, reason=d.get("reason", ""),
            outcome=_OUTCOME_MAP.get(str(d.get("outcome", "pending")), "PENDING"),
            before_metrics={"detection_signal": d.get("detection_signal")},
            is_reversible=action in _REVERSIBLE,
        )
        if action in _NONTRIVIAL:
            incident.status = "RECOVERING"
            incident.severity = severity
            incident.save(update_fields=["status", "severity"])
        return Response(ActionLogSerializer(log).data, status=status.HTTP_201_CREATED)

    def get(self, request):
        qs = ActionLog.objects.select_related("model_version__model", "incident").all()
        if request.query_params.get("action"):
            qs = qs.filter(action=_ACTION_MAP.get(request.query_params["action"],
                                                  request.query_params["action"]))
        qs = qs[:int(request.query_params.get("limit", 50))]
        return Response(ActionLogSerializer(qs, many=True).data)


class ActionDetailView(APIView):
    def patch(self, request, pk):
        try:
            log = ActionLog.objects.select_related("incident").get(pk=pk)
        except ActionLog.DoesNotExist:
            return Response({"error": {"code": "not_found"}},
                            status=status.HTTP_404_NOT_FOUND)
        d = request.data or {}
        if "outcome" in d:
            log.outcome = _OUTCOME_MAP.get(str(d["outcome"]), log.outcome)
        log.executed_at = timezone.now()
        if "after_metrics" in d:
            log.after_metrics = d["after_metrics"]
        log.save()

        ver = d.get("verification")
        if ver is not None:
            recovered = bool(ver.get("recovered"))
            escalate = bool(ver.get("escalate_to_human"))
            decision = "KEEP" if recovered else ("ESCALATE" if escalate else "REVERT")
            VerificationResult.objects.update_or_create(
                action=log, defaults={
                    "success": recovered, "decision": decision,
                    "post_metrics": ver})
            # Close the incident on a verified recovery / escalation.
            inc = log.incident
            inc.status = "RESOLVED" if recovered else "ESCALATED"
            inc.closed_at = timezone.now()
            inc.save(update_fields=["status", "closed_at"])
        return Response(ActionLogSerializer(log).data)
