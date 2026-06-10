from django.urls import path

from .views import ActiveModelView, ModelListView

urlpatterns = [
    path("active-model", ActiveModelView.as_view()),
    path("models", ModelListView.as_view()),
]
