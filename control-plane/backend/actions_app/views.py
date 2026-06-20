"""actions_app views — POST/GET /api/actions, PATCH /api/actions/{id}.

Translates the agent's flat action vocabulary (api_contracts.md §B.3) into the
normalized Incident/ActionLog/VerificationResult rows (data_model.md §5).
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from api_common import error_response, parse_limit, resolve_version
from .models import ActionLog, Incident, VerificationResult
from .serializers import ActionLogSerializer

# Which DB actions are non-trivial enough to drive an incident into RECOVERING.
_NONTRIVIAL = {"SWITCH", "ROLLBACK", "RETRAIN", "DISABLE"}

# Actions the agent can automatically undo: a traffic SWITCH can be switched back,
# a ROLLBACK re-applied, DISABLE re-enabled. NO_OP/ALERT have nothing to revert and
# RETRAIN is not cleanly reversible. (The old `action in _NONTRIVIAL or action in
# ("NO_OP","ALERT")` covered every action, so the flag was always True.)
_REVERSIBLE = {"SWITCH", "ROLLBACK", "DISABLE"}


class ActionsView(APIView):
    def post(self, request):
        d = request.data or {}
        action = ActionLog.action_from_agent(d.get("action", "no_op"))
        target = (str(d.get("target_model") or "").strip()) or "model_a"
        version = resolve_version(target)
        severity = str(d.get("severity", "LOW")).upper()
        incident = Incident.open_or_reuse(version, severity)

        log = ActionLog.objects.create(
            incident=incident, model_version=version, action=action,
            severity=severity, reason=d.get("reason", ""),
            outcome=ActionLog.outcome_from_agent(d.get("outcome", "pending")),
            before_metrics={"detection_signal": d.get("detection_signal")},
            is_reversible=action in _REVERSIBLE,
        )
        if action in _NONTRIVIAL:
            incident.begin_recovery(severity)
        return Response(ActionLogSerializer(log).data, status=status.HTTP_201_CREATED)

    def get(self, request):
        qs = ActionLog.objects.select_related("model_version__model", "incident").all()
        if request.query_params.get("action"):
            param = request.query_params["action"]
            qs = qs.filter(action=ActionLog.action_from_agent(param, default=param))
        qs = qs[:parse_limit(request)]
        return Response(ActionLogSerializer(qs, many=True).data)


class ActionDetailView(APIView):
    def patch(self, request, pk):
        try:
            log = ActionLog.objects.select_related("incident").get(pk=pk)
        except ActionLog.DoesNotExist:
            return error_response("not_found", status_code=status.HTTP_404_NOT_FOUND)
        d = request.data or {}
        if "outcome" in d:
            log.outcome = ActionLog.outcome_from_agent(d["outcome"], default=log.outcome)
        # Stamp execution time only when an outcome is actually being recorded, and
        # only once — re-PATCHing (e.g. a later verification update) must not move
        # an already-executed action's timestamp forward, nor fabricate one for a
        # verification-only call. This keeps the append-only audit timeline honest.
        if "outcome" in d and log.executed_at is None:
            log.executed_at = timezone.now()
        if "after_metrics" in d:
            log.after_metrics = d["after_metrics"]
        log.save()

        ver = d.get("verification")
        if ver is not None:
            recovered = bool(ver.get("recovered"))
            decision = VerificationResult.decide(recovered, bool(ver.get("escalate_to_human")))
            VerificationResult.objects.update_or_create(
                action=log, defaults={
                    "success": recovered, "decision": decision,
                    "post_metrics": ver})
            # The incident drives its own close transition from the verdict.
            log.incident.apply_verification(decision)
        return Response(ActionLogSerializer(log).data)
