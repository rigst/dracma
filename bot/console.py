"""Console web do assistente.

Mesmo agente, mesmas ferramentas, mesmo domínio que o Telegram — só o
transporte muda. É o que permite alguém experimentar o produto inteiro no
navegador, sem instalar nada e sem abrir conversa com bot nenhum.
"""

from __future__ import annotations

import logging

from django.conf import settings

from ai.agente import Contexto, SemQuota, responder
from carteira.models import Origem

from .canais.console import ConsoleCanal
from .models import Mensagem

logger = logging.getLogger(__name__)

SEM_QUOTA = (
    "Sua cota de conversas deste mês acabou 😕 "
    "Ela reinicia no dia 1º — enquanto isso, dá pra lançar tudo pelo portal."
)


def historico(usuario, limite: int | None = None):
    consulta = Mensagem.objects.filter(usuario=usuario, conta__isnull=True).order_by("-criada_em")
    return list(reversed(list(consulta[: limite or settings.AI_HISTORICO_TURNOS * 2])))


def conversar(usuario, texto: str) -> tuple[Mensagem, Mensagem]:
    """Uma rodada no console. Devolve (pergunta, resposta)."""
    texto = (texto or "").strip()[: settings.AI_MAX_CHARS_MENSAGEM]

    pergunta = Mensagem.objects.create(
        usuario=usuario,
        canal=ConsoleCanal.nome,
        direcao=Mensagem.Direcao.ENTRADA,
        tipo=Mensagem.Tipo.TEXTO,
        texto=texto,
        status=Mensagem.Status.PROCESSANDO,
    )

    anteriores = _anteriores(usuario, pergunta)
    turno = None

    try:
        resultado = responder(
            Contexto(espaco=usuario.espaco, usuario=usuario, origem=Origem.PORTAL),
            texto,
            historico=anteriores,
        )
        saida, turno = resultado.texto, resultado.turno
        status = Mensagem.Status.RESPONDIDA
    except SemQuota:
        saida, status = SEM_QUOTA, Mensagem.Status.RESPONDIDA
    except Exception:
        logger.exception("Console falhou para o usuário %s", usuario.pk)
        saida, status = "Deu um problema aqui 😕 Tenta de novo?", Mensagem.Status.ERRO

    Mensagem.objects.filter(pk=pergunta.pk).update(status=status)
    pergunta.status = status

    resposta = Mensagem.objects.create(
        usuario=usuario,
        canal=ConsoleCanal.nome,
        direcao=Mensagem.Direcao.SAIDA,
        tipo=Mensagem.Tipo.TEXTO,
        texto=saida or "Ok!",
        status=status,
        turno=turno or None,
    )
    return pergunta, resposta


def _anteriores(usuario, pergunta) -> list[dict]:
    """Histórico do console, com os blocos de tool dos turnos anteriores.

    Mesma razão do Telegram (ver `bot.tasks._historico`): só texto faz o modelo
    ler a própria confirmação como narração e refazer a escrita.
    """
    historico_montado: list[dict] = []

    for m in historico(usuario):
        if m.pk == pergunta.pk:
            continue
        if m.direcao == Mensagem.Direcao.ENTRADA:
            if m.conteudo:
                historico_montado.append({"role": "user", "content": m.conteudo})
        elif m.turno:
            historico_montado.extend(m.turno)
        elif m.conteudo:
            historico_montado.append({"role": "assistant", "content": m.conteudo})

    while historico_montado and historico_montado[0]["role"] != "user":
        historico_montado.pop(0)
    return historico_montado
