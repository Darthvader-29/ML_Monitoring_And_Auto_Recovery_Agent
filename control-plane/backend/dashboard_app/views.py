"""dashboard_app — read-only, server-rendered operator UI (dashboard.md).

A single pane of glass answering: are my models healthy, is the data drifting, and
what has the agent done? Pure consumer of registry_app / monitoring_app /
actions_app via the ORM (dashboard.md §2); adds no persistence of its own.
"""
from __future__ import annotations

from django.shortcuts import render

from actions_app.models import ActionLog, Incident
from monitoring_app.models import MetricSnapshot
from registry_app.models import ActiveModelPointer, Model


def _model_cards() -> list[dict]:
    cards = []
    for model in Model.objects.all():
        snap = (MetricSnapshot.objects
                .filter(model_version__model=model).order_by("-timestamp").first())
        active = model.versions.filter(is_active=True).exists()
        cards.append({"name": model.model_name, "active": active, "snap": snap})
    return cards


def overview(request):
    pointer = (ActiveModelPointer.objects
               .select_related("model_version__model").filter(pk=1).first())
    context = {
        "active": pointer.model_version if pointer else None,
        "switched_at": pointer.switched_at if pointer else None,
        "switched_by": pointer.switched_by if pointer else None,
        "cards": _model_cards(),
        "actions": (ActionLog.objects
                    .select_related("model_version__model")
                    .order_by("-decided_at")[:15]),
        "open_incidents": (Incident.objects
                           .exclude(status__in=["RESOLVED", "ESCALATED"]).count()),
        "refresh_seconds": 10,
    }
    return render(request, "dashboard/overview.html", context)
