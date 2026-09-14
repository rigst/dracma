from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("visitante/", views.entrar_visitante, name="entrar_visitante"),
]
