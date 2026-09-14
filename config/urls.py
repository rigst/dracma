"""URLs do projeto Centavo."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from legal import views as legal_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Páginas públicas das políticas, servidas da versão vigente no banco.
    path("privacidade/", legal_views.privacidade, name="privacidade"),
    path("termos/", legal_views.termos, name="termos"),
    path("legal/", include("legal.urls")),
    path("accounts/", include("accounts.urls")),
    path("zap/", include("zap.urls")),
    path("", include("carteira.urls")),
]
