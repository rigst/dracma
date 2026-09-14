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
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotFound, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.limites import excedeu_limite

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
    """Conversa com a Centavo pelo navegador.

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
