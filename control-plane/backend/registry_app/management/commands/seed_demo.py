"""Seed the registry for the demo: model_a (ACTIVE) + model_b (BACKUP).

Idempotent. Creates the two logical models, one version each (matching the trained
artifacts and their held-out metrics), points the active slot at model_a, and
captures a current Baseline per version for the VERIFY phase.
Run: python manage.py seed_demo
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from monitoring_app.models import Baseline
from registry_app.models import ActiveModelPointer, Model, ModelVersion

SEED = [
    ("model_a", "1.0.0", "http://localhost:8001", 8001,
     {"accuracy": 0.846, "f1": 0.731}, 0.005),
    ("model_b", "0.9.0", "http://localhost:8002", 8002,
     {"accuracy": 0.798, "f1": 0.625}, 0.005),
]


class Command(BaseCommand):
    help = "Seed registry with model_a/model_b and point active at model_a."

    def handle(self, *args, **options):
        versions = {}
        for name, ver, url, port, metrics, err in SEED:
            model, _ = Model.objects.get_or_create(
                model_name=name, defaults={"description": f"{name} classifier"})
            mv, _ = ModelVersion.objects.get_or_create(
                model=model, version=ver,
                defaults={"artifact_path": f"model-services/{name}/model.pkl",
                          "endpoint_url": url, "port": port,
                          "metrics_at_training": metrics, "status": "STABLE"})
            versions[name] = mv
            Baseline.objects.get_or_create(
                model_version=mv, is_current=True,
                defaults={"metrics": metrics, "ref_accuracy": metrics["accuracy"],
                          "ref_error_rate": err, "ref_p95_latency_ms": 100.0,
                          "note": f"seed baseline for {name}@{ver}"})

        ActiveModelPointer.switch_to(versions["model_a"], by="seed")
        self.stdout.write(self.style.SUCCESS(
            "Seeded model_a (ACTIVE) + model_b (BACKUP); active -> model_a"))

        # The API requires a token by default, so mint one for the agent and surface
        # it. Operators export it as DJANGO_API_TOKEN so the agent can authenticate.
        try:
            from django.contrib.auth import get_user_model
            from rest_framework.authtoken.models import Token
            user, _ = get_user_model().objects.get_or_create(username="agent")
            token, _ = Token.objects.get_or_create(user=user)
            self.stdout.write(self.style.SUCCESS(
                f"Agent API token: {token.key}\n"
                f"  export DJANGO_API_TOKEN={token.key}   # for the agent"))
        except Exception as exc:  # authtoken not installed (DJANGO_REQUIRE_AUTH=0)
            self.stdout.write(f"(token auth disabled — skipping token: {exc})")
