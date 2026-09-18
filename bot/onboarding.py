"""Primeiros passos, no Telegram e no portal.

Duas metades do mesmo problema:

- **No portal** a pessoa descobre COMO conectar. Num desktop ela não consegue
  tocar num link que abre o Telegram do celular, então a página oferece os três
  caminhos: QR para ler com o celular, deep link `t.me` para quem já está no
  próprio telefone, e o código para digitar à mão.
- **No Telegram** ela descobre O QUE fazer depois de conectar. Um "pronto,
  conectei" sozinho deixa a pessoa olhando para uma conversa vazia sem saber que
  pode mandar áudio, foto de comprovante ou pedir um limite.

O roteiro tem três etapas e para sozinho. Mais que isso vira spam. Mesmo sem
a régua de qualidade que a Meta aplicava, o custo aqui é direto: o botão de
bloquear fica a um toque de distância.

Nenhum texto usa Markdown. O envio é em texto puro (ver `bot.canais.telegram`),
então um `*` aqui apareceria literal na conversa.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import SafeString, mark_safe

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
Pronto, conectei esta conversa à sua conta ✅

Eu sou a Dracma. Daqui pra frente é só me contar seus gastos, do jeito que for \
mais fácil:

💬 escrevendo: “almocei 32 reais no cartão”
🎤 mandando áudio: útil quando você está na rua
📸 foto do comprovante ou print do PIX
📄 PDF do extrato ou do boleto

Me manda o primeiro pra gente começar 👇\
"""

DICA_LIMITE = """\
Boa, primeiro lançamento registrado 🎉

Agora o mais útil: me peça um limite e eu aviso antes de estourar, não depois \
que a fatura fechou.

Experimenta: “cria um limite de R$ 400 pra delivery”

E quando quiser saber como está o mês, é só perguntar: “quanto sobra esse \
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

Esta conversa ainda não está ligada a nenhuma conta. Entre no portal, abra \
“Conectar Telegram” e toque no botão de conectar. Ou me mande aqui o código \
de 6 dígitos que aparece na tela.

{url}\
"""


def _url(rota: str = "carteira:painel") -> str:
    base = (settings.SITE_URL or "").rstrip("/")
    return f"{base}{reverse(rota)}"


def texto_convite() -> str:
    return CONVITE_PAREAMENTO.format(url=_url("bot:conectar"))


def link_telegram(token: str) -> str:
    """Deep link que abre o bot já mandando o `/start` com o token.

    É a grande vantagem do Telegram no pareamento: a pessoa toca uma vez e o
    token chega sozinho, sem digitar, sem copiar, sem errar dígito.
    """
    usuario_bot = (settings.TELEGRAM_BOT_USERNAME or "").strip().lstrip("@")
    if not usuario_bot:
        return ""
    return f"https://t.me/{usuario_bot}?start={token}"


def gerar_codigo(usuario):
    """Credencial válida para este usuário, reaproveitando uma vigente.

    Reaproveitar importa: sem isso, cada recarga da página inventaria um código
    novo e o que a pessoa já tinha anotado (ou o QR que já fotografou) pararia
    de funcionar.
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


def qr_svg(conteudo: str) -> SafeString:
    """QR em SVG inline, pronto para o template.

    Marcado como seguro aqui, e não com um `|safe` na tela: o SVG é gerado pelo
    segno a partir de um link que este módulo monta, sem nada digitado por
    ninguém. Dizer isso no template obrigaria quem lê a vir até aqui conferir.

    É o que resolve o desktop: a pessoa está no computador e precisa levar o
    link para o celular. Sem cor fixa: as classes deixam o CSS pintar, então o
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
    return mark_safe(buffer.getvalue().decode())  # SVG do segno, sem entrada de usuário


def avancar(conta, canal=None) -> bool:
    """Manda a próxima mensagem do roteiro, se houver. True se enviou.

    Chamado depois de cada interação bem-sucedida. As etapas são gravadas antes
    do envio, para uma falha de entrega não deixar a pessoa presa recebendo a
    mesma dica para sempre.
    """
    from . import envio
    from .models import ContaTelegram

    etapa = conta.onboarding_etapa
    if etapa >= CONCLUIDO:
        return False

    if etapa == NAO_INICIADO:
        texto, proxima = BOAS_VINDAS, CONECTADO
    elif etapa == CONECTADO:
        texto, proxima = DICA_LIMITE, PRIMEIRO_LANCAMENTO
    else:
        texto, proxima = DICA_PORTAL.format(url=_url()), CONCLUIDO

    ContaTelegram.objects.filter(pk=conta.pk).update(onboarding_etapa=proxima)
    conta.onboarding_etapa = proxima

    envio.responder(conta, texto, canal=canal)
    return True
