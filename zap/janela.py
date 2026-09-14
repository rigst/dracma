"""A janela de atendimento de 24 horas da Meta.

Regra da plataforma, não preferência nossa: dentro de 24h desde a ÚLTIMA
mensagem do usuário, a resposta pode ter qualquer formato; fora disso, só
template previamente aprovado no painel da Meta.

Isso significa que **um alerta proativo não pode simplesmente ser enviado**.
A decisão mora aqui, num lugar só — se ficasse espalhada como um `if` em cada
ponto de envio, a primeira task nova esqueceria dela e as mensagens passariam
a falhar em silêncio na Meta.
"""

from __future__ import annotations

import logging
from enum import Enum

from django.conf import settings
from django.utils import timezone

from .canais import obter_canal
from .models import JanelaAtendimento, Mensagem, NumeroWhatsApp

logger = logging.getLogger(__name__)


class Decisao(Enum):
    LIVRE = "livre"
    """Janela aberta: manda texto normal."""

    TEMPLATE = "template"
    """Janela fechada, mas há template aprovado configurado."""

    ADIAR = "adiar"
    """Janela fechada e sem template. O alerta é registrado como adiado e
    entregue na próxima vez que a pessoa falar — melhor do que uma falha de
    entrega repetida, que a Meta pune desabilitando a subscrição."""


def registrar_inbound(numero: NumeroWhatsApp) -> JanelaAtendimento:
    """Reabre a janela. Chamado a cada mensagem recebida do usuário."""
    janela, _ = JanelaAtendimento.objects.update_or_create(
        numero=numero, defaults={"ultimo_inbound_em": timezone.now()}
    )
    return janela


def janela_aberta(numero: NumeroWhatsApp) -> bool:
    janela = JanelaAtendimento.objects.filter(numero=numero).first()
    return bool(janela and janela.aberta)


def decidir(numero: NumeroWhatsApp, template: str = "") -> Decisao:
    if janela_aberta(numero):
        return Decisao.LIVRE
    return Decisao.TEMPLATE if template else Decisao.ADIAR


def responder(numero: NumeroWhatsApp, texto: str, canal=None) -> Mensagem:
    """Resposta a uma mensagem do usuário.

    Sempre livre: por definição, responder acontece dentro da janela que a
    própria mensagem acabou de abrir.
    """
    canal = canal or obter_canal()
    resultado = canal.enviar_texto(numero.numero, texto)

    return Mensagem.objects.create(
        numero=numero,
        usuario=numero.usuario,
        canal=canal.nome,
        direcao=Mensagem.Direcao.SAIDA,
        tipo=Mensagem.Tipo.TEXTO,
        texto=texto,
        wamid=resultado.id_externo or None,
        status=Mensagem.Status.RESPONDIDA if resultado.entregue else Mensagem.Status.ERRO,
        erro=resultado.erro,
    )


def notificar(
    numero: NumeroWhatsApp,
    texto: str,
    *,
    template: str = "",
    parametros: list[str] | None = None,
    canal=None,
) -> tuple[Decisao, Mensagem | None]:
    """Mensagem PROATIVA — alerta de limite, lembrete de vencimento, resumo.

    Diferente de `responder`: aqui somos nós que iniciamos, então a janela
    precisa ser consultada antes.

    Devolve a decisão tomada junto com a mensagem, para quem chamou registrar
    o alerta como enviado ou adiado.
    """
    canal = canal or obter_canal()
    decisao = decidir(numero, template if canal.suporta_template() else "")

    if decisao is Decisao.ADIAR:
        logger.info(
            "Notificação adiada para %s: janela fechada e sem template configurado.",
            numero.numero,
        )
        return decisao, None

    if decisao is Decisao.LIVRE:
        resultado = canal.enviar_texto(numero.numero, texto)
        tipo = Mensagem.Tipo.TEXTO
    else:
        resultado = canal.enviar_template(numero.numero, template, parametros or [texto])
        tipo = Mensagem.Tipo.TEMPLATE

    mensagem = Mensagem.objects.create(
        numero=numero,
        usuario=numero.usuario,
        canal=canal.nome,
        direcao=Mensagem.Direcao.SAIDA,
        tipo=tipo,
        texto=texto,
        wamid=resultado.id_externo or None,
        status=Mensagem.Status.RESPONDIDA if resultado.entregue else Mensagem.Status.ERRO,
        erro=resultado.erro,
    )
    return decisao, mensagem


def template_para(tipo_alerta: str) -> str:
    """Nome do template utility aprovado para cada tipo de alerta.

    Vazio = não configurado, e o alerta será adiado em vez de enviado.
    """
    from carteira.models import Alerta

    mapa = {
        Alerta.Tipo.LIMITE_PROXIMO: settings.WHATSAPP_TEMPLATE_LIMITE,
        Alerta.Tipo.LIMITE_ESTOURADO: settings.WHATSAPP_TEMPLATE_LIMITE,
        Alerta.Tipo.VENCIMENTO: settings.WHATSAPP_TEMPLATE_VENCIMENTO,
    }
    return mapa.get(tipo_alerta, "")
