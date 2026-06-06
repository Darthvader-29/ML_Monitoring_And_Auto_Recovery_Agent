from django.urls import path

from .views import LatestMetricsView, MetricsView

urlpatterns = [
    path("metrics", MetricsView.as_view()),
    path("metrics/latest", LatestMetricsView.as_view()),
]
