from django.urls import path

from .views import ActionDetailView, ActionsView

urlpatterns = [
    path("actions", ActionsView.as_view()),
    path("actions/<int:pk>", ActionDetailView.as_view()),
]
