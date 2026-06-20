"""actions_app models — the auditable Decide->Act->Verify trail (data_model.md §5)."""
from __future__ import annotations

from django.db import models
from django.utils import timezone

from monitoring_app.models import Baseline
from registry_app.models import ModelVersion


class Incident(models.Model):
    """A degradation episode affecting one ModelVersion; groups ActionLogs."""

    INCIDENT_STATUS = [
        ("OPEN", "Open"), ("RECOVERING", "Recovering"), ("VERIFYING", "Verifying"),
        ("RESOLVED", "Resolved"), ("ESCALATED", "Escalated"),
    ]
    SEVERITY = [("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High"),
                ("CRITICAL", "Critical")]
    CATEGORY = [
        ("DATA_DRIFT", "Data drift"), ("CONCEPT_DRIFT", "Concept drift"),
        ("ANOMALY", "Anomaly"), ("THRESHOLD", "Threshold breach"),
        ("AVAILABILITY", "Availability / health"), ("UNKNOWN", "Unknown"),
    ]

    affected_version = models.ForeignKey(ModelVersion, on_delete=models.PROTECT,
                                         related_name="incidents")
    opened_at = models.DateTimeField(auto_now_add=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=INCIDENT_STATUS,
                              default="OPEN", db_index=True)
    severity = models.CharField(max_length=8, choices=SEVERITY, default="LOW",
                                db_index=True)
    category = models.CharField(max_length=24, choices=CATEGORY, default="UNKNOWN")
    root_cause = models.TextField(blank=True, default="")

    TERMINAL = ("RESOLVED", "ESCALATED")

    class Meta:
        ordering = ["-opened_at"]

    # ---- lifecycle state machine (owned here, not in the view) ----------
    # OPEN --(nontrivial action)--> RECOVERING --+--(KEEP)----> RESOLVED  (terminal)
    #                                            +--(ESCALATE)-> ESCALATED (terminal)
    #                                            +--(REVERT)---> RECOVERING (stays open)

    @classmethod
    def open_or_reuse(cls, version: ModelVersion, severity: str) -> "Incident":
        """Reuse the latest non-terminal incident for this version, else open one."""
        inc = (version.incidents.exclude(status__in=cls.TERMINAL)
               .order_by("-opened_at").first())
        return inc or cls.objects.create(affected_version=version, severity=severity)

    def begin_recovery(self, severity: str) -> None:
        self.status, self.severity = "RECOVERING", severity
        self.save(update_fields=["status", "severity"])

    def resolve(self) -> None:
        self.status, self.closed_at = "RESOLVED", timezone.now()
        self.save(update_fields=["status", "closed_at"])

    def escalate(self) -> None:
        self.status, self.closed_at = "ESCALATED", timezone.now()
        self.save(update_fields=["status", "closed_at"])

    def keep_recovering(self) -> None:
        """A REVERT verdict: the recovery failed and was undone — keep the incident
        open for another attempt rather than force-closing it."""
        self.status, self.closed_at = "RECOVERING", None
        self.save(update_fields=["status", "closed_at"])

    def apply_verification(self, decision: str) -> None:
        """Drive the close transition from a VERIFY verdict (KEEP/ESCALATE/REVERT)."""
        {"KEEP": self.resolve,
         "ESCALATE": self.escalate,
         "REVERT": self.keep_recovering}[decision]()


class ActionLog(models.Model):
    """Immutable, append-only audit record of one decision + its execution."""

    ACTION = [
        ("NO_OP", "No-op"), ("ALERT", "Alert only"), ("SWITCH", "Switch active model"),
        ("ROLLBACK", "Rollback version"), ("RETRAIN", "Retrain"),
        ("DISABLE", "Disable predictions"),
    ]
    SEVERITY = Incident.SEVERITY
    OUTCOME = [("PENDING", "Pending"), ("SUCCESS", "Success"),
               ("FAILED", "Failed"), ("SKIPPED", "Skipped"), ("REVERTED", "Reverted")]

    # Agent (api_contracts.md §B.3) lowercase vocab -> DB enum. Lives next to the
    # choices it bridges, so the two vocabularies have one source of truth.
    _AGENT_ACTION = {
        "no_op": "NO_OP", "alert": "ALERT", "switch_backup": "SWITCH",
        "rollback": "ROLLBACK", "retrain": "RETRAIN", "disable_predictions": "DISABLE",
    }
    _AGENT_OUTCOME = {"pending": "PENDING", "success": "SUCCESS", "failed": "FAILED",
                      "skipped": "SKIPPED", "reverted": "REVERTED"}

    @classmethod
    def action_from_agent(cls, value, default: str = "NO_OP") -> str:
        return cls._AGENT_ACTION.get(str(value), default)

    @classmethod
    def outcome_from_agent(cls, value, default: str = "PENDING") -> str:
        # A skipped action did NOT succeed (target already active) — it maps to its
        # own SKIPPED outcome, never silently to SUCCESS.
        return cls._AGENT_OUTCOME.get(str(value), default)

    incident = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name="actions")
    model_version = models.ForeignKey(ModelVersion, on_delete=models.PROTECT,
                                      related_name="actions")
    action = models.CharField(max_length=16, choices=ACTION, db_index=True)
    severity = models.CharField(max_length=8, choices=SEVERITY)
    reason = models.TextField()
    decided_at = models.DateTimeField(auto_now_add=True, db_index=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=8, choices=OUTCOME, default="PENDING",
                               db_index=True)
    jenkins_build_id = models.CharField(max_length=64, blank=True, default="")
    before_metrics = models.JSONField(default=dict, blank=True)
    after_metrics = models.JSONField(default=dict, blank=True)
    is_reversible = models.BooleanField(default=True)
    reverted_by = models.ForeignKey("self", on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name="reverts")

    class Meta:
        ordering = ["-decided_at"]


class VerificationResult(models.Model):
    """The VERIFY-phase verdict for one executed ActionLog."""

    VERIFY_DECISION = [("KEEP", "Keep"), ("REVERT", "Revert"), ("ESCALATE", "Escalate")]

    action = models.OneToOneField(ActionLog, on_delete=models.CASCADE,
                                  related_name="verification")
    baseline_ref = models.ForeignKey(Baseline, on_delete=models.PROTECT,
                                     related_name="verifications", null=True, blank=True)
    verified_at = models.DateTimeField(auto_now_add=True)
    post_metrics = models.JSONField(default=dict)
    success = models.BooleanField()
    decision = models.CharField(max_length=8, choices=VERIFY_DECISION)

    class Meta:
        ordering = ["-verified_at"]

    @staticmethod
    def decide(recovered: bool, escalate: bool) -> str:
        """Map a VERIFY outcome to a verdict: recovered -> KEEP; else escalate ->
        ESCALATE; else REVERT (recovery failed but a retry is still possible)."""
        return "KEEP" if recovered else ("ESCALATE" if escalate else "REVERT")
