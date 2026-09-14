from django.urls import path

from . import views

app_name = "zap"

urlpatterns = [
    path("webhook/", views.webhook, name="webhook"),
]
