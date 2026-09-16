from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("visitante/", views.entrar_visitante, name="entrar_visitante"),
    # Auto-cadastro, respondem 404 enquanto SIGNUP_ENABLED for False.
    path("cadastro/", views.cadastrar, name="cadastro"),
    path("cadastro/enviado/", views.cadastro_enviado, name="cadastro_enviado"),
    path("cadastro/confirmar/<uidb64>/<token>/", views.confirmar_email, name="confirmar_email"),
]
