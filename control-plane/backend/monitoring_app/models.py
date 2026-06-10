"""monitoring_app models — persisted Observe output (data_model.md §4)."""
from __future__ import annotations

from django.db import models

from registry_app.models import ModelVersion


class MetricSnapshot(models.Model):
    """One aggregated observation of a ModelVersion over a time window."""

    HEALTH_STATUS = [
        ("HEALTHY", "Healthy"), ("DEGRADED", "Degraded"),
        ("UNHEALTHY", "Unhealthy"), ("UNKNOWN", "Unknown"),
    ]

    model_version = models.ForeignKey(ModelVersion, on_delete=models.CASCADE,
                                      related_name="snapshots")
    timestamp = models.DateTimeField(db_index=True)
    window_seconds = models.PositiveIntegerField(default=60)
    request_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    error_rate = models.FloatField(default=0.0)
    avg_latency_ms = models.FloatField(default=0.0)
    p95_latency_ms = models.FloatField(default=0.0)
    avg_confidence = models.FloatField(null=True, blank=True)
    accuracy = models.FloatField(null=True, blank=True)
    f1 = models.FloatField(null=True, blank=True)
    rmse = models.FloatField(null=True, blank=True)
    missing_rate = models.FloatField(default=0.0)
    out_of_range_rate = models.FloatField(default=0.0)
    overall_drift_score = models.FloatField(default=0.0)
    drifted_feature_count = models.PositiveIntegerField(default=0)
    health_status = models.CharField(max_length=16, choices=HEALTH_STATUS,
                                     default="HEALTHY", db_index=True)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        get_latest_by = "timestamp"
        indexes = [
            models.Index(fields=["model_version", "-timestamp"], name="idx_snap_version_ts"),
            models.Index(fields=["health_status"], name="idx_snap_health"),
        ]


class FeatureDriftScore(models.Model):
    """Per-feature drift breakdown of a single MetricSnapshot."""

    snapshot = models.ForeignKey(MetricSnapshot, on_delete=models.CASCADE,
                                 related_name="feature_drift")
    feature_name = models.CharField(max_length=128)
    psi = models.FloatField(null=True, blank=True)
    ks_stat = models.FloatField(null=True, blank=True)
    ks_pvalue = models.FloatField(null=True, blank=True)
    drifted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["snapshot", "feature_name"]
        constraints = [
            models.UniqueConstraint(fields=["snapshot", "feature_name"],
                                    name="uq_drift_snapshot_feature"),
        ]


class Baseline(models.Model):
    """A known-good reference metric set for a ModelVersion (used by VERIFY)."""

    model_version = models.ForeignKey(ModelVersion, on_delete=models.CASCADE,
                                      related_name="baselines")
    captured_at = models.DateTimeField(auto_now_add=True)
    is_current = models.BooleanField(default=True, db_index=True)
    metrics = models.JSONField(default=dict)
    ref_accuracy = models.FloatField(null=True, blank=True)
    ref_error_rate = models.FloatField(null=True, blank=True)
    ref_p95_latency_ms = models.FloatField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-captured_at"]
        constraints = [
            models.UniqueConstraint(fields=["model_version"],
                                    condition=models.Q(is_current=True),
                                    name="uq_one_current_baseline_per_version"),
        ]
