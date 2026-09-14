from django.urls import path

from . import views

app_name = "carteira"

urlpatterns = [
    path("", views.painel, name="painel"),
    path("transacoes/", views.transacoes, name="transacoes"),
    path("limites/", views.limites, name="limites"),
    path("relatorios/", views.relatorios, name="relatorios"),
    path("exportar/", views.exportar, name="exportar"),
]
