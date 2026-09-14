from django.urls import path

from . import views

app_name = "zap"

urlpatterns = [
    path("webhook/", views.webhook, name="webhook"),
    path("console/", views.console, name="console"),
    path("conectar/", views.conectar, name="conectar"),
    path("conectar/email/", views.enviar_instrucoes, name="enviar_instrucoes"),
    path("conectar/desconectar/", views.desconectar, name="desconectar"),
]
