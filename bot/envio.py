"""Saída de mensagens: o único lugar que grava o log do que o bot falou.

Este módulo é o que sobrou de `zap.janela`, e o encolhimento é a maior
vantagem prática da troca do WhatsApp pelo Telegram. A Meta só deixava
responder em formato livre dentro de 24h desde a última mensagem do usuário;
fora disso, exigia um template previamente aprovado no painel. Isso obrigava
todo envio proativo a consultar uma janela, escolher entre texto e template, e
adiar quando não houvesse nenhum aprovado.

No Telegram nada disso existe: depois do `/start`, o bot escreve quando
quiser. Sobrou o que de fato importa, mandar, registrar, e parar de insistir
com quem bloqueou o bot.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from .canais import obter_canal
from .models import ContaTelegram, Mensagem

logger = logging.getLogger(__name__)


def responder(conta: ContaTelegram, texto: str, canal=None, turno=None) -> Mensagem:
    """Resposta a uma mensagem do usuário.

    `turno` é o transcript do agente (com os blocos de tool), guardado para o
    próximo turno reproduzir. Vazio nas mensagens que não vieram do agente,
    como as do roteiro de onboarding.
    """
    return _enviar(conta, texto, canal=canal, turno=turno)


def notificar(conta: ContaTelegram, texto: str, canal=None) -> tuple[bool, Mensagem | None]:
    """Mensagem PROATIVA, alerta de limite, lembrete de vencimento, resumo.

    Devolve (alcançou, mensagem). Diferente de `responder` em um ponto só: uma
    conta bloqueada é pulada antes de gastar a chamada de rede, já que o
    Telegram recusaria de qualquer forma.
    """
    if conta.bloqueado_em is not None:
        logger.info("Notificação pulada: a conta %s bloqueou o bot.", conta.pk)
        return False, None

    mensagem = _enviar(conta, texto, canal=canal)
    return mensagem.status != Mensagem.Status.ERRO, mensagem


def _enviar(conta: ContaTelegram, texto: str, canal=None, turno=None) -> Mensagem:
    canal = canal or obter_canal()
    resultado = canal.enviar_texto(conta.chat_id, texto)

    if resultado.bloqueado:
        # Permanente: sem isto, todo alerta futuro viraria uma chamada de rede
        # inútil por rodada do beat, para sempre. O vínculo NÃO é desfeito: a
        # pessoa pode desbloquear, e o histórico continua sendo dela.
        ContaTelegram.objects.filter(pk=conta.pk).update(bloqueado_em=timezone.now())
        conta.bloqueado_em = timezone.now()
        logger.warning("Conta %s marcada como bloqueada: %s", conta.pk, resultado.erro[:200])
    elif conta.bloqueado_em is not None:
        # Entregou de novo: a pessoa desbloqueou. Limpar a marca é o que
        # devolve os alertas para ela sem precisar de intervenção manual.
        ContaTelegram.objects.filter(pk=conta.pk).update(bloqueado_em=None)
        conta.bloqueado_em = None

    return Mensagem.objects.create(
        conta=conta,
        usuario=conta.usuario,
        canal=canal.nome,
        direcao=Mensagem.Direcao.SAIDA,
        tipo=Mensagem.Tipo.TEXTO,
        texto=texto,
        id_externo=resultado.id_externo or None,
        status=Mensagem.Status.RESPONDIDA if resultado.entregue else Mensagem.Status.ERRO,
        erro=resultado.erro,
        turno=turno or None,
    )
