"""Console web do assistente.

Mesmo agente, mesmas ferramentas, mesmo domínio que o WhatsApp — só o
transporte muda. É o que permite alguém experimentar o produto inteiro no
navegador sem estar na allowlist de 5 números do número de teste da Meta.
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
    consulta = Mensagem.objects.filter(usuario=usuario, numero__isnull=True).order_by("-criada_em")
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

    anteriores = [
        {
            "role": "user" if m.direcao == Mensagem.Direcao.ENTRADA else "assistant",
            "content": m.conteudo,
        }
        for m in historico(usuario)
        if m.pk != pergunta.pk and m.conteudo
    ]

    try:
        saida = responder(
            Contexto(espaco=usuario.espaco, usuario=usuario, origem=Origem.PORTAL),
            texto,
            historico=anteriores,
        ).texto
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
    )
    return pergunta, resposta
