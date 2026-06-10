"""Tests for metrics ingestion (/api/metrics)."""
import json

from django.test import Client, TestCase

from .models import MetricSnapshot


class MetricsTests(TestCase):
    def test_post_metrics_creates_snapshot(self):
        c = Client()
        resp = c.post("/api/metrics", data=json.dumps({
            "model_name": "model_a", "model_version": "1.0.0", "error_rate": 0.6,
            "avg_latency_ms": 8, "p95_latency_ms": 20, "status": "degraded"}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(MetricSnapshot.objects.count(), 1)
        snap = MetricSnapshot.objects.first()
        self.assertEqual(snap.health_status, "DEGRADED")
        self.assertAlmostEqual(snap.error_rate, 0.6)

    def test_get_metrics_lists(self):
        c = Client()
        for _ in range(3):
            c.post("/api/metrics", data=json.dumps(
                {"model_name": "model_a", "model_version": "1.0.0"}),
                content_type="application/json")
        self.assertEqual(len(c.get("/api/metrics").json()), 3)
