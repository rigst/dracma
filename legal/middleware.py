"""Interstitial de re-aceite.

Quando uma versão material é publicada, todo usuário autenticado é levado à tela de
re-aceite antes de continuar usando o sistema. Como efeito colateral desejado, as
contas criadas antes deste app — que nunca registraram aceite — passam pelo mesmo
caminho no primeiro login, o que dispensa migração de dados.
"""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from .services import precisa_reaceitar

# Prefixos que precisam continuar acessíveis, sob pena de prender o usuário num
# laço de redirecionamento (ou impedi-lo de ler aquilo que deve aceitar).
PREFIXOS_LIVRES = (
    "/legal/",
    "/termos/",
    "/privacidade/",
    "/logout/",
    "/admin/",
    "/static/",
    "/media/",
    "/healthz",
    "/favicon.ico",
)


class AceiteObrigatorioMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # Cada projeto acrescenta o que for próprio dele. No trilhas são as
        # rotas do PWA (/sw.js, /offline/): se o service worker levasse 302
        # para a tela de aceite, o navegador cacharia o redirecionamento.
        self.prefixos = PREFIXOS_LIVRES + tuple(getattr(settings, "LEGAL_ALLOWLIST_EXTRA", ()))

    def __call__(self, request):
        usuario = getattr(request, "user", None)
        if (
            usuario is not None
            and usuario.is_authenticated
            and not request.path.startswith(self.prefixos)
            and precisa_reaceitar(usuario)
        ):
            return redirect(reverse("legal:reaceite"))
        return self.get_response(request)
