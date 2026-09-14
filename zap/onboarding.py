"""Primeiros passos, no WhatsApp e no portal.

Duas metades do mesmo problema:

- **No portal** a pessoa descobre COMO conectar. Num desktop ela não consegue
  tocar num link que abre o WhatsApp do celular, então a página oferece os três
  caminhos: QR para ler com o celular, link `wa.me` para quem está no próprio
  telefone, e o código para digitar à mão.
- **No WhatsApp** ela descobre O QUE fazer depois de conectar. Um "pronto,
  conectei" sozinho deixa a pessoa olhando para uma conversa vazia sem saber que
  pode mandar áudio, foto de comprovante ou pedir um limite.

O roteiro tem três etapas e para sozinho. Mais que isso vira spam, e a Meta
trata volume de mensagem não solicitada como sinal de qualidade ruim.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

# Etapas do roteiro.
NAO_INICIADO = 0
CONECTADO = 1  # boas-vindas enviadas
PRIMEIRO_LANCAMENTO = 2  # já registrou algo; recebeu a dica de limite
CONCLUIDO = 3

# Validade do código de pareamento. Curta de propósito: é credencial de vínculo
# de conta, e quem gera está com a página aberta.
VALIDADE_CODIGO = timedelta(minutes=30)

BOAS_VINDAS = """\
Pronto, conectei este número à sua conta ✅

Eu sou a Dracma. Daqui pra frente é só me contar seus gastos, do jeito que for \
mais fácil:

💬 escrevendo — “almocei 32 reais no cartão”
🎤 mandando áudio — útil quando você está na rua
📸 foto do comprovante ou print do PIX
📄 PDF do extrato ou do boleto

Me manda o primeiro pra gente começar 👇\
"""

DICA_LIMITE = """\
Boa, primeiro lançamento registrado 🎉

Agora o mais útil: me peça um limite e eu aviso *antes* de estourar, não depois \
que a fatura fechou.

Experimenta: “cria um limite de R$ 400 pra delivery”

E quando quiser saber como está o mês, é só perguntar — “quanto sobra esse \
mês?” ou “dá pra comprar um tênis de 420?”\
"""

DICA_PORTAL = """\
Mais uma e eu paro de atrapalhar 😄

No portal você vê tudo junto: gráficos do mês, contas, e dá pra exportar em CSV.

{url}

Qualquer coisa, é só me chamar por aqui 💜\
"""

CONVITE_PAREAMENTO = """\
Oi! Eu sou a Dracma 💜

Este número ainda não está ligado a nenhuma conta. Entre no portal, abra \
*Conectar WhatsApp* e me mande o código de 6 dígitos que aparece lá.

{url}\
"""


def _url(rota: str = "carteira:painel") -> str:
    base = (settings.SITE_URL or "").rstrip("/")
    return f"{base}{reverse(rota)}"


def texto_convite() -> str:
    return CONVITE_PAREAMENTO.format(url=_url("zap:conectar"))


def link_wa_me(codigo: str) -> str:
    """Deep link que abre a conversa com o texto já preenchido.

    O número aqui é o NOSSO, o do bot: `wa.me` monta uma conversa com ele, e o
    `text` é o que a pessoa vai enviar — o código de pareamento.
    """
    numero = (settings.WHATSAPP_NUMERO or "").strip()
    if not numero:
        return ""
    return f"https://wa.me/{numero}?text={codigo}"


def gerar_codigo(usuario):
    """Código válido para este usuário, reaproveitando um vigente.

    Reaproveitar importa: sem isso, cada recarga da página inventaria um código
    novo e o que a pessoa já tinha anotado pararia de funcionar.
    """
    from .models import CodigoPareamento

    vigente = (
        CodigoPareamento.objects.filter(
            usuario=usuario, usado_em__isnull=True, expira_em__gt=timezone.now()
        )
        .order_by("-criado_em")
        .first()
    )
    if vigente is not None:
        return vigente

    return CodigoPareamento.objects.create(
        usuario=usuario, expira_em=timezone.now() + VALIDADE_CODIGO
    )


def qr_svg(conteudo: str) -> str:
    """QR em SVG inline.

    É o que resolve o desktop: a pessoa está no computador e precisa levar o
    link para o celular. Sem cor fixa — as classes deixam o CSS pintar, então o
    QR acompanha o tema claro e escuro.
    """
    import io

    import segno

    buffer = io.BytesIO()
    segno.make(conteudo, error="m").save(
        buffer,
        kind="svg",
        xmldecl=False,
        svgns=False,
        omitsize=True,
        scale=4,
        border=2,
        svgclass="qr",
        lineclass="qr-linha",
    )
    return buffer.getvalue().decode()


def avancar(numero, canal=None) -> bool:
    """Manda a próxima mensagem do roteiro, se houver. True se enviou.

    Chamado depois de cada interação bem-sucedida. As etapas são gravadas antes
    do envio, para uma falha de entrega não deixar a pessoa presa recebendo a
    mesma dica para sempre.
    """
    from . import janela
    from .models import NumeroWhatsApp

    etapa = numero.onboarding_etapa
    if etapa >= CONCLUIDO:
        return False

    if etapa == NAO_INICIADO:
        texto, proxima = BOAS_VINDAS, CONECTADO
    elif etapa == CONECTADO:
        texto, proxima = DICA_LIMITE, PRIMEIRO_LANCAMENTO
    else:
        texto, proxima = DICA_PORTAL.format(url=_url()), CONCLUIDO

    NumeroWhatsApp.objects.filter(pk=numero.pk).update(onboarding_etapa=proxima)
    numero.onboarding_etapa = proxima

    janela.responder(numero, texto, canal=canal)
    return True
