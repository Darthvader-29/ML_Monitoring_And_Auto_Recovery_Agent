"""registry_app models — what models/versions exist and which serves traffic.

Transcribed from data_model.md §3 (the authoritative schema). The active version is
flipped atomically via ActiveModelPointer.switch_to so the system can never observe
two active (or zero active) versions.
"""
from __future__ import annotations

from django.db import models, transaction


class Model(models.Model):
    """A logical model identity (e.g. 'model_a'). Owns many versions."""

    TASK_TYPES = [
        ("binary_classification", "Binary classification"),
        ("multiclass_classification", "Multiclass classification"),
        ("regression", "Regression"),
    ]

    model_name = models.CharField(max_length=64, unique=True, db_index=True)
    description = models.TextField(blank=True, default="")
    task_type = models.CharField(max_length=32, choices=TASK_TYPES,
                                 default="binary_classification")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["model_name"]

    def __str__(self) -> str:
        return self.model_name


class ModelVersion(models.Model):
    """A concrete versioned artifact of a Model."""

    VERSION_STATUS = [
        ("CANDIDATE", "Candidate"),
        ("STABLE", "Stable"),
        ("ACTIVE", "Active"),
        ("DEPRECATED", "Deprecated"),
        ("ROLLED_BACK", "Rolled back"),
    ]

    model = models.ForeignKey(Model, on_delete=models.PROTECT, related_name="versions")
    version = models.CharField(max_length=32)
    artifact_path = models.CharField(max_length=255)
    trained_at = models.DateTimeField(null=True, blank=True)
    training_dataset_ref = models.CharField(max_length=255, blank=True, default="")
    metrics_at_training = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=VERSION_STATUS,
                              default="CANDIDATE", db_index=True)
    is_active = models.BooleanField(default=False, db_index=True)
    endpoint_url = models.CharField(max_length=255, blank=True, default="")
    port = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["model__model_name", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["model", "version"],
                                    name="uq_modelversion_model_version"),
            models.UniqueConstraint(fields=["is_active"], condition=models.Q(is_active=True),
                                    name="uq_one_global_active_version"),
        ]

    def __str__(self) -> str:
        return f"{self.model.model_name}@{self.version} [{self.status}]"


class ActiveModelPointer(models.Model):
    """Singleton row: the single ModelVersion currently serving traffic."""

    model_version = models.OneToOneField(ModelVersion, on_delete=models.PROTECT,
                                         related_name="active_pointer")
    switched_at = models.DateTimeField(auto_now=True)
    switched_by = models.CharField(max_length=64, default="agent")

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"ACTIVE -> {self.model_version}"

    @classmethod
    @transaction.atomic
    def switch_to(cls, version: "ModelVersion", by: str = "agent") -> "ActiveModelPointer":
        """Atomically promote `version` to active and demote the previous one.

        Adapted from data_model.md §3.3 for A/B failover: there is ONE global serving
        slot across model_a and model_b, so every other active version is demoted
        (not just versions of the same logical model)."""
        (ModelVersion.objects
            .select_for_update()
            .filter(is_active=True)
            .exclude(pk=version.pk)
            .update(is_active=False, status="STABLE"))
        version.is_active = True
        version.status = "ACTIVE"
        version.save(update_fields=["is_active", "status"])
        pointer, _ = cls.objects.get_or_create(
            pk=1, defaults={"model_version": version, "switched_by": by})
        pointer.model_version = version
        pointer.switched_by = by
        pointer.save()
        return pointer
