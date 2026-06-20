"""Tests for the registry: atomic single-active invariant + /api/active-model."""
import json

from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings

from .models import ActiveModelPointer, Model, ModelVersion


class RegistryTests(TestCase):
    def setUp(self):
        self.a = ModelVersion.objects.create(
            model=Model.objects.create(model_name="model_a"), version="1.0.0",
            artifact_path="x", endpoint_url="http://model_a:8001", port=8001)
        self.b = ModelVersion.objects.create(
            model=Model.objects.create(model_name="model_b"), version="0.9.0",
            artifact_path="x", endpoint_url="http://model_b:8002", port=8002)

    def test_switch_keeps_single_active(self):
        ActiveModelPointer.switch_to(self.a)
        ActiveModelPointer.switch_to(self.b)
        self.assertEqual(ModelVersion.objects.filter(is_active=True).count(), 1)
        self.assertTrue(ModelVersion.objects.get(pk=self.b.pk).is_active)
        self.assertFalse(ModelVersion.objects.get(pk=self.a.pk).is_active)

    def test_db_rejects_second_direct_active(self):
        """The DB enforces a single globally-active version even when is_active is
        written directly (bypassing switch_to)."""
        self.a.is_active = True
        self.a.save(update_fields=["is_active"])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.b.is_active = True
                self.b.save(update_fields=["is_active"])
        self.assertEqual(ModelVersion.objects.filter(is_active=True).count(), 1)

    def test_active_model_endpoint_flip(self):
        ActiveModelPointer.switch_to(self.a)
        c = Client()
        self.assertEqual(c.get("/api/active-model").json()["model_name"], "model_a")
        resp = c.post("/api/active-model", data=json.dumps({"model_name": "model_b"}),
                      content_type="application/json")
        self.assertEqual(resp.json()["model_name"], "model_b")
        self.assertEqual(c.get("/api/active-model").json()["model_name"], "model_b")

    def test_auto_select_skips_retired_versions(self):
        # A STABLE version, then a DEPRECATED one created LATER (newest by created_at).
        stable = ModelVersion.objects.create(
            model=self.a.model, version="1.1.0", artifact_path="x",
            endpoint_url="http://model_a:8003", port=8003, status="STABLE")
        ModelVersion.objects.create(
            model=self.a.model, version="2.0.0", artifact_path="x",
            endpoint_url="http://model_a:8004", port=8004, status="DEPRECATED")
        c = Client()
        resp = c.post("/api/active-model",
                      data=json.dumps({"model_name": "model_a", "reason": "promote stable"}),
                      content_type="application/json")
        body = resp.json()
        # The STABLE version is chosen, not the newer DEPRECATED one.
        self.assertEqual(body["version"], stable.version)
        self.assertEqual(body["reason"], "promote stable")


class TopologyExposureTests(TestCase):
    """GET /api/models hides internal topology when the flag is off."""

    def setUp(self):
        ModelVersion.objects.create(
            model=Model.objects.create(model_name="model_a"), version="1.0.0",
            artifact_path="x", endpoint_url="http://model_a:8001", port=8001)

    def test_topology_present_by_default(self):
        row = Client().get("/api/models").json()[0]
        self.assertIn("endpoint_url", row)
        self.assertIn("port", row)
        self.assertEqual(row["endpoint_url"], "http://model_a:8001")
        self.assertEqual(row["port"], 8001)

    @override_settings(EXPOSE_INTERNAL_TOPOLOGY=False)
    def test_topology_hidden_when_flag_off(self):
        row = Client().get("/api/models").json()[0]
        self.assertNotIn("endpoint_url", row)
        self.assertNotIn("port", row)
        # Non-sensitive fields remain.
        self.assertIn("model_name", row)
        self.assertIn("version", row)
