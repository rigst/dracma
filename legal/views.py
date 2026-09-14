"""Páginas públicas dos documentos legais e o interstitial de re-aceite."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from .forms import AceiteForm
from .models import AceiteLegal, DocumentoLegal, OrigemAceite, StatusDocumento, TipoDocumento
from .services import (
    documentos_pendentes,
    documentos_vigentes,
    historico,
    registrar_aceite,
)


def _destino_pos_aceite():
    """Para onde levar o usuário depois de aceitar.

    Configurável porque o app é copiado entre projetos com rotas iniciais
    diferentes: `dashboard` onde há login, `/` no divisor, que não tem contas.
    """
    destino = getattr(settings, "LEGAL_REDIRECT_URL", "/")
    try:
        return reverse(destino)
    except Exception:
        return destino


def _documento_vigente_ou_404(tipo):
    documento = DocumentoLegal.objects.vigente(tipo)
    if documento is None:
        raise Http404("Nenhuma versão publicada deste documento.")
    return documento


def _pagina_documento(request, tipo):
    documento = _documento_vigente_ou_404(tipo)
    return render(
        request,
        "legal/documento.html",
        {
            "documento": documento,
            "versoes": historico(tipo),
            "e_versao_vigente": True,
        },
    )


@require_GET
def termos(request):
    return _pagina_documento(request, TipoDocumento.TERMOS)


@require_GET
def privacidade(request):
    return _pagina_documento(request, TipoDocumento.PRIVACIDADE)


@require_GET
def versao(request, tipo, versao):
    """Versão específica, inclusive arquivada — transparência sobre o histórico."""
    if tipo not in TipoDocumento.values:
        raise Http404("Tipo de documento desconhecido.")
    documento = get_object_or_404(
        DocumentoLegal,
        tipo=tipo,
        versao=versao,
        status__in=[StatusDocumento.PUBLICADO, StatusDocumento.ARQUIVADO],
    )
    return render(
        request,
        "legal/documento.html",
        {
            "documento": documento,
            "versoes": historico(tipo),
            "e_versao_vigente": documento.status == StatusDocumento.PUBLICADO,
        },
    )


@require_GET
def aceite_visitante(request):
    """Tela de aceite antes de criar uma conta de visitante.

    Existe como página, e não como checkbox no login, por dois motivos: o texto
    que a pessoa precisa ler cabe numa página e não num canto do formulário, e o
    aceite deixa de ser um detalhe ao lado de um botão para ser o próprio passo.

    O formulário posta na rota que cada projeto usa para criar o visitante
    (`LEGAL_VISITOR_ACTION`), com os campos extras que aquela view espera
    (`LEGAL_VISITOR_EXTRA`) — é o que mantém esta tela igual entre os sistemas
    sem que o app `legal` precise saber como cada um cria a conta.
    """
    destino = getattr(settings, "LEGAL_VISITOR_ACTION", None)
    if not destino:
        raise Http404("Acesso de visitante não configurado neste sistema.")

    vigentes = documentos_vigentes()
    if not vigentes:
        raise Http404("Nenhum documento publicado.")

    return render(
        request,
        "legal/aceite.html",
        {
            "form": AceiteForm(),
            "documentos": list(vigentes.values()),
            "action": reverse(destino),
            "campos_extras": getattr(settings, "LEGAL_VISITOR_EXTRA", {}),
        },
    )


@login_required
def reaceite(request):
    """Bloqueia o uso do sistema até a nova versão ser aceita."""
    pendentes = documentos_pendentes(request.user)
    if not pendentes:
        return redirect(_destino_pos_aceite())

    if request.method == "POST":
        form = AceiteForm(request.POST)
        if form.is_valid():
            registrar_aceite(
                request,
                usuario=request.user,
                origem=OrigemAceite.REACEITE,
                documentos=pendentes,
                e_visitante=request.user.username.startswith("visitante_"),
            )
            messages.success(request, "Obrigado. Aceite registrado.")
            return redirect(_destino_pos_aceite())
    else:
        form = AceiteForm()

    return render(
        request,
        "legal/reaceite.html",
        {"form": form, "documentos": pendentes},
    )


@require_GET
@login_required
def meus_aceites(request):
    """Comprovante do próprio usuário — LGPD art. 18, direito de acesso."""
    aceites = (
        AceiteLegal.objects.filter(usuario=request.user)
        .select_related("documento")
        .order_by("-aceito_em")
    )
    return render(request, "legal/meus_aceites.html", {"aceites": aceites})
