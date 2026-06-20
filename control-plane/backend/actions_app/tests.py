"""Tests for the audit trail: POST /api/actions + PATCH verify."""
import json

from django.test import Client, TestCase, override_settings

from .models import ActionLog, Incident, VerificationResult


class ActionsTests(TestCase):
    def test_post_action_creates_log_and_incident(self):
        c = Client()
        resp = c.post("/api/actions", data=json.dumps({
            "action": "switch_backup", "severity": "HIGH", "target_model": "model_a",
            "reason": "error_rate 0.6", "outcome": "pending"}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        log = ActionLog.objects.get()
        self.assertEqual(log.action, "SWITCH")
        self.assertEqual(Incident.objects.count(), 1)
        self.assertEqual(log.incident.status, "RECOVERING")

    def test_patch_action_records_verification_and_closes_incident(self):
        c = Client()
        aid = c.post("/api/actions", data=json.dumps({
            "action": "switch_backup", "severity": "HIGH", "target_model": "model_a",
            "reason": "x", "outcome": "pending"}),
            content_type="application/json").json()["id"]
        resp = c.patch(f"/api/actions/{aid}", data=json.dumps({
            "outcome": "success",
            "verification": {"recovered": True, "model_checked": "model_b"}}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["outcome"], "SUCCESS")
        v = VerificationResult.objects.get()
        self.assertTrue(v.success)
        self.assertEqual(v.decision, "KEEP")
        self.assertEqual(Incident.objects.get().status, "RESOLVED")

    def test_skipped_outcome_is_not_recorded_as_success(self):
        c = Client()
        resp = c.post("/api/actions", data=json.dumps({
            "action": "switch_backup", "severity": "HIGH", "target_model": "model_a",
            "reason": "target already active", "outcome": "skipped"}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(ActionLog.objects.get().outcome, "SKIPPED")


class MetricExposureTests(TestCase):
    """GET /api/actions hides raw operational metric blobs when the flag is off."""

    def _create_action(self):
        Client().post("/api/actions", data=json.dumps({
            "action": "switch_backup", "severity": "HIGH", "target_model": "model_a",
            "reason": "x", "outcome": "pending", "detection_signal": "error_rate"}),
            content_type="application/json")

    def test_metrics_present_by_default(self):
        self._create_action()
        row = Client().get("/api/actions").json()[0]
        self.assertIn("before_metrics", row)
        self.assertIn("after_metrics", row)

    @override_settings(EXPOSE_INTERNAL_TOPOLOGY=False)
    def test_metrics_hidden_when_flag_off(self):
        self._create_action()
        row = Client().get("/api/actions").json()[0]
        self.assertNotIn("before_metrics", row)
        self.assertNotIn("after_metrics", row)
        # Non-sensitive fields remain.
        self.assertIn("action", row)
        self.assertIn("outcome", row)

    def test_is_reversible_is_meaningful(self):
        c = Client()
        for act, expected in [("switch_backup", True), ("alert", False), ("no_op", False)]:
            ActionLog.objects.all().delete()
            c.post("/api/actions", data=json.dumps({
                "action": act, "severity": "HIGH", "target_model": "model_a",
                "reason": "r", "outcome": "pending"}),
                content_type="application/json")
            self.assertEqual(ActionLog.objects.get().is_reversible, expected)
