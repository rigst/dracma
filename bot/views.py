"""Endpoint do webhook da Bot API.

Regra que manda nesta view: **responder 200 em milissegundos, sempre.**

O Telegram re-tenta o update quando a resposta demora ou falha e, com falhas
repetidas, vai espaçando as entregas até praticamente parar: o bot fica mudo
e ninguém é avisado. Por isso a view não chama a Claude, não baixa mídia e não
toca no domínio: confere o segredo, grava o cru e entrega o resto ao Celery.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotFound,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.limites import excedeu_limite
from legal.utils import ip_do_request

from . import onboarding
from .console import conversar
from .models import ContaTelegram, Mensagem
from .webhook import extrair_mensagens, token_valido

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def webhook(request):
    if not settings.TELEGRAM_ENABLED:
        return HttpResponseNotFound()

    if not token_valido(request.headers.get("X-Telegram-Bot-Api-Secret-Token")):
        logger.warning("Webhook com segredo inválido recusado.")
        return HttpResponseForbidden("segredo inválido")

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        # 200 mesmo assim: reenviar não vai consertar um corpo malformado, e
        # insistir só faz o Telegram espaçar as entregas seguintes.
        logger.warning("Webhook com JSON inválido.")
        return JsonResponse({"status": "ignorado"})

    for item in extrair_mensagens(payload):
        _enfileirar(item, payload)

    return JsonResponse({"status": "ok"})


def _enfileirar(item: dict, payload: dict) -> None:
    """Grava a mensagem crua e dispara a task. Idempotente pelo id externo."""
    from .tasks import processar_mensagem

    conta, _ = ContaTelegram.objects.get_or_create(
        chat_id=item["chat_id"],
        defaults={"username": item["username"], "primeiro_nome": item["primeiro_nome"]},
    )

    mensagem, nova = Mensagem.objects.get_or_create(
        id_externo=item["id_externo"],
        defaults={
            "conta": conta,
            "usuario": conta.usuario,
            "canal": "telegram",
            "direcao": Mensagem.Direcao.ENTRADA,
            "tipo": item["tipo"],
            "texto": item["texto"],
            "payload": payload,
        },
    )

    if not nova:
        # O Telegram reenvia o mesmo update quando não vê o 200 a tempo. Sem
        # esta saída, a mesma fala do usuário viraria duas transações.
        logger.info("Update %s já processado; ignorando reenvio.", item["id_externo"])
        return

    processar_mensagem.delay(mensagem.pk, item.get("file_id") or "", item.get("mime_type") or "")


@login_required
def console(request):
    """Conversa com a Dracma pelo navegador.

    Mesmo agente do Telegram; só o transporte muda. Com HTMX, o POST devolve
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
                "bot/_falas.html",
                {"falas": [], "aviso": "Devagar aí 😄 Espera um minutinho e manda de novo."},
            )

        pergunta, resposta = conversar(request.user, texto)
        return render(request, "bot/_falas.html", {"falas": [pergunta, resposta]})

    # A conversa mora no painel; não há tela separada para ela.
    return redirect("carteira:painel")


# ---------------------------------------------------------------------------
# Conectar o Telegram
# ---------------------------------------------------------------------------


@login_required
def conectar(request):
    """Instruções de configuração.

    Três caminhos porque o ponto de partida muda: num desktop a pessoa não
    consegue tocar num link que abre o Telegram do celular, então o QR é o que
    funciona; no próprio telefone, o deep link resolve em um toque; e o código
    digitado à mão cobre quem já tem a conversa aberta em outro aparelho.
    """
    contas = list(request.user.contas_telegram.all())
    conectado = next((c for c in contas if c.vinculado), None)

    contexto = {
        "conectado": conectado,
        "bot_username": (settings.TELEGRAM_BOT_USERNAME or "").lstrip("@"),
        "telegram_habilitado": settings.TELEGRAM_ENABLED,
    }

    if conectado is None:
        codigo = onboarding.gerar_codigo(request.user)
        link = onboarding.link_telegram(codigo.token)
        contexto.update(
            {
                "codigo": codigo.codigo,
                "expira_em": codigo.expira_em,
                "link": link,
                # Sem o @username do bot não há o que codificar; a tela cai nas
                # instruções manuais em vez de mostrar um QR quebrado.
                "qr": onboarding.qr_svg(link) if link else "",
            }
        )

    return render(request, "bot/_conectar.html", contexto)


@login_required
@require_POST
def enviar_instrucoes(request):
    """Manda as instruções por e-mail.

    Existe para o caminho mais comum do desktop: a pessoa está no computador,
    quer as instruções no celular, e o e-mail é o canal que ela já tem nos dois.
    """
    if not request.user.email:
        messages.error(request, "Sua conta não tem e-mail cadastrado.")
        return redirect("carteira:painel")

    # O envio é gratuito para quem dispara e custa reputação de domínio se
    # virar rajada.
    if excedeu_limite(f"instrucoes:{ip_do_request(request)}", limite=5, janela_s=3600):
        messages.error(request, "Muitos envios deste endereço. Tente daqui a pouco.")
        return redirect("carteira:painel")

    codigo = onboarding.gerar_codigo(request.user)
    corpo = render_to_string(
        "bot/instrucoes_email.txt",
        {
            "usuario": request.user,
            "codigo": codigo.codigo,
            "expira_em": codigo.expira_em,
            "bot_username": (settings.TELEGRAM_BOT_USERNAME or "").lstrip("@"),
            "link": onboarding.link_telegram(codigo.token),
            "site": (settings.SITE_URL or "").rstrip("/"),
        },
    )
    enviados = send_mail(
        subject="Como conectar seu Telegram à Dracma",
        message=corpo,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[request.user.email],
        fail_silently=True,
    )

    if enviados:
        messages.success(request, f"Instruções enviadas para {request.user.email}.")
    else:
        messages.error(request, "Não consegui enviar o e-mail agora. Tente de novo.")
    return redirect("carteira:painel")


@login_required
@require_POST
def desconectar(request):
    """Desfaz o vínculo da conversa.

    A conta em si não é apagada: ela guarda o histórico de mensagens. O que sai
    é o vínculo, e com ele o acesso ao espaço.
    """
    atualizados = ContaTelegram.objects.filter(usuario=request.user).update(
        usuario=None, verificado_em=None, onboarding_etapa=onboarding.NAO_INICIADO
    )
    if atualizados:
        messages.info(request, "Telegram desconectado.")
    return redirect("carteira:painel")
