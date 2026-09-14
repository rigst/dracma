"""Endpoint do webhook da Meta.

Regra que manda nesta view: **responder 200 em milissegundos, sempre.**

A Meta re-tenta o webhook quando a resposta demora ou falha e, com falhas
repetidas, desabilita a subscrição — o app para de receber mensagem e ninguém
é avisado. Por isso a view não chama a Claude, não baixa mídia e não toca no
domínio: valida a assinatura, grava o cru e entrega o resto ao Celery.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from accounts.limites import excedeu_limite
from legal.utils import ip_do_request

from . import onboarding
from .console import conversar, historico
from .models import Mensagem, NumeroWhatsApp
from .webhook import assinatura_valida, extrair_mensagens, verificar_handshake

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request):
    if not settings.WHATSAPP_ENABLED:
        return HttpResponseNotFound()

    if request.method == "GET":
        desafio = verificar_handshake(request.GET)
        if desafio is None:
            return HttpResponseForbidden("verify token inválido")
        # Texto puro, sem aspas: a Meta compara byte a byte.
        return HttpResponse(desafio, content_type="text/plain")

    # `request.body` e não `json.loads(...)` reserializado: o HMAC é sobre os
    # bytes exatos que a Meta assinou.
    corpo = request.body
    if not assinatura_valida(corpo, request.headers.get("X-Hub-Signature-256")):
        logger.warning("Webhook com assinatura inválida recusado.")
        return HttpResponseForbidden("assinatura inválida")

    try:
        payload = json.loads(corpo)
    except json.JSONDecodeError:
        # 200 mesmo assim: reenviar não vai consertar um corpo malformado, e
        # insistir só aproxima a Meta de desabilitar a subscrição.
        logger.warning("Webhook com JSON inválido.")
        return JsonResponse({"status": "ignorado"})

    for item in extrair_mensagens(payload):
        _enfileirar(item, payload)

    return JsonResponse({"status": "ok"})


def _enfileirar(item: dict, payload: dict) -> None:
    """Grava a mensagem crua e dispara a task. Idempotente pelo wamid."""
    from .tasks import processar_mensagem

    numero, _ = NumeroWhatsApp.objects.get_or_create(numero=item["de"])

    mensagem, nova = Mensagem.objects.get_or_create(
        wamid=item["wamid"],
        defaults={
            "numero": numero,
            "usuario": numero.usuario,
            "canal": "cloud_api",
            "direcao": Mensagem.Direcao.ENTRADA,
            "tipo": item["tipo"],
            "texto": item["texto"],
            "payload": payload,
        },
    )

    if not nova:
        # A Meta reenvia o mesmo webhook por conta própria. Sem esta saída, a
        # mesma fala do usuário viraria duas transações.
        logger.info("wamid %s já processado; ignorando reenvio.", item["wamid"])
        return

    processar_mensagem.delay(mensagem.pk, item.get("media_id") or "")


@login_required
def console(request):
    """Conversa com a Dracma pelo navegador.

    Mesmo agente do WhatsApp; só o transporte muda. Com HTMX, o POST devolve
    só o par de falas novas, e não a página inteira.
    """
    if request.method == "POST":
        texto = request.POST.get("mensagem", "")
        if not texto.strip():
            return HttpResponse(status=204)

        chave = f"console:{request.user.pk}"
        if excedeu_limite(chave, settings.AI_LIMITE_MENSAGENS, settings.AI_JANELA_S):
            return render(
                request,
                "zap/_falas.html",
                {"falas": [], "aviso": "Devagar aí 😄 Espera um minutinho e manda de novo."},
            )

        pergunta, resposta = conversar(request.user, texto)
        return render(request, "zap/_falas.html", {"falas": [pergunta, resposta]})

    return render(
        request,
        "zap/console.html",
        {
            "falas": historico(request.user, limite=60),
            "max_chars": settings.AI_MAX_CHARS_MENSAGEM,
        },
    )


# ---------------------------------------------------------------------------
# Conectar o WhatsApp
# ---------------------------------------------------------------------------


@login_required
def conectar(request):
    """Instruções de configuração.

    Três caminhos porque o ponto de partida muda: num desktop a pessoa não
    consegue tocar num link que abre o WhatsApp do celular, então o QR é o que
    funciona; no próprio telefone, o `wa.me` resolve em um toque; e o código
    digitado à mão cobre quem já tem a conversa aberta.
    """
    numeros = list(request.user.numeros.all())
    conectado = next((n for n in numeros if n.vinculado), None)

    contexto = {
        "conectado": conectado,
        "numero_bot": settings.WHATSAPP_NUMERO,
        "whatsapp_habilitado": settings.WHATSAPP_ENABLED,
    }

    if conectado is None:
        codigo = onboarding.gerar_codigo(request.user)
        link = onboarding.link_wa_me(codigo.codigo)
        contexto.update(
            {
                "codigo": codigo.codigo,
                "expira_em": codigo.expira_em,
                "link": link,
                # Sem número configurado não há o que codificar; a tela cai nas
                # instruções manuais em vez de mostrar um QR quebrado.
                "qr": onboarding.qr_svg(link) if link else "",
            }
        )

    return render(request, "zap/conectar.html", contexto)


@login_required
@require_POST
def enviar_instrucoes(request):
    """Manda as instruções por e-mail.

    Existe para o caminho mais comum do desktop: a pessoa está no computador,
    quer as instruções no celular, e o e-mail é o canal que ela já tem nos dois.
    """
    if not request.user.email:
        messages.error(request, "Sua conta não tem e-mail cadastrado.")
        return redirect("zap:conectar")

    # O envio é gratuito para quem dispara e custa reputação de domínio se
    # virar rajada.
    if excedeu_limite(f"instrucoes:{ip_do_request(request)}", limite=5, janela_s=3600):
        messages.error(request, "Muitos envios deste endereço. Tente daqui a pouco.")
        return redirect("zap:conectar")

    codigo = onboarding.gerar_codigo(request.user)
    corpo = render_to_string(
        "zap/instrucoes_email.txt",
        {
            "usuario": request.user,
            "codigo": codigo.codigo,
            "expira_em": codigo.expira_em,
            "numero_bot": settings.WHATSAPP_NUMERO,
            "link": onboarding.link_wa_me(codigo.codigo),
            "site": (settings.SITE_URL or "").rstrip("/"),
        },
    )
    enviados = send_mail(
        subject="Como conectar seu WhatsApp à Dracma",
        message=corpo,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[request.user.email],
        fail_silently=True,
    )

    if enviados:
        messages.success(request, f"Instruções enviadas para {request.user.email}.")
    else:
        messages.error(request, "Não consegui enviar o e-mail agora. Tente de novo.")
    return redirect("zap:conectar")


@login_required
@require_POST
def desconectar(request):
    """Desfaz o vínculo do número.

    O número em si não é apagado: ele guarda o histórico de mensagens. O que sai
    é o vínculo, e com ele o acesso ao espaço.
    """
    atualizados = NumeroWhatsApp.objects.filter(usuario=request.user).update(
        usuario=None, verificado_em=None, onboarding_etapa=onboarding.NAO_INICIADO
    )
    if atualizados:
        messages.info(request, "WhatsApp desconectado.")
    return redirect("zap:conectar")
