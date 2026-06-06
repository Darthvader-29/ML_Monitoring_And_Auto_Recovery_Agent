"""Root URL conf — all REST routes mounted under /api/ (api_contracts.md §B)."""
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("api/health/", health),
    path("api/", include("registry_app.urls")),
    path("api/", include("monitoring_app.urls")),
    path("api/", include("actions_app.urls")),
]
