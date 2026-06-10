"""Tests for the audit trail: POST /api/actions + PATCH verify."""
import json

from django.test import Client, TestCase

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
