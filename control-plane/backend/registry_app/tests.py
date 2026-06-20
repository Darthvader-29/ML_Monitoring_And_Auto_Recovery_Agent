"""Tests for the registry: atomic single-active invariant + /api/active-model."""
import json

from django.db import IntegrityError, transaction
from django.test import Client, TestCase

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
