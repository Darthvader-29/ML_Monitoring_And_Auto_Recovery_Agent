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

    def test_limit_param_is_validated(self):
        c = Client()
        for _ in range(3):
            c.post("/api/metrics", data=json.dumps(
                {"model_name": "model_a", "model_version": "1.0.0"}),
                content_type="application/json")
        # non-integer -> falls back to default (no 500)
        self.assertEqual(c.get("/api/metrics?limit=abc").status_code, 200)
        # negative -> default, no crash
        self.assertEqual(c.get("/api/metrics?limit=-5").status_code, 200)
        self.assertEqual(len(c.get("/api/metrics?limit=2").json()), 2)

    def test_bad_timestamp_does_not_crash_and_falls_back(self):
        c = Client()
        r = c.post("/api/metrics", data=json.dumps({
            "model_name": "model_a", "model_version": "1.0.0",
            "timestamp": "not-a-date"}), content_type="application/json")
        self.assertEqual(r.status_code, 201)
        self.assertIsNotNone(MetricSnapshot.objects.first().timestamp)

    def test_valid_iso_timestamp_is_stored_aware(self):
        from django.utils import timezone as tz
        c = Client()
        c.post("/api/metrics", data=json.dumps({
            "model_name": "model_a", "model_version": "1.0.0",
            "timestamp": "2026-06-20T10:00:00+00:00"}),
            content_type="application/json")
        ts = MetricSnapshot.objects.first().timestamp
        self.assertFalse(tz.is_naive(ts))
        self.assertEqual(ts.year, 2026)
