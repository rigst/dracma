"""URLs do projeto Dracma."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts import views as accounts_views
from legal import views as legal_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", accounts_views.Entrar.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Recuperação de senha, nas views prontas do Django com os templates do
    # projeto em templates/registration/.
    path(
        "senha/",
        auth_views.PasswordResetView.as_view(
            email_template_name="registration/password_reset_email.html",
            subject_template_name="registration/password_reset_subject.txt",
        ),
        name="password_reset",
    ),
    path(
        "senha/enviado/",
        auth_views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "senha/confirmar/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "senha/pronto/",
        auth_views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    # Páginas públicas das políticas, servidas da versão vigente no banco.
    path("privacidade/", legal_views.privacidade, name="privacidade"),
    path("termos/", legal_views.termos, name="termos"),
    path("legal/", include("legal.urls")),
    path("accounts/", include("accounts.urls")),
    path("bot/", include("bot.urls")),
    path("", include("carteira.urls")),
]
