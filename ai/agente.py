"""O agente: loop de tool use sobre a Claude.

Loop manual, e não o tool runner do SDK. Três razões:

1. As tools têm efeito colateral no banco, e aqui o controle do que roda, em
   que ordem e sob qual quota é explícito.
2. O loop é curto — uma ou duas iterações resolvem quase tudo.
3. Um loop próprio é trivial de testar com um cliente falso, que é como o resto
   da frota testa (unittest.mock puro, sem factory_boy).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.utils import timezone

from accounts.quota import registrar_consumo, tem_quota
from carteira.models import Categoria, Conta, Origem

from . import tools
from .cliente import obter_cliente
from .prompts import INSTRUCOES, contexto_do_espaco

logger = logging.getLogger(__name__)


class SemQuota(Exception):
    """A quota mensal de tokens acabou."""


@dataclass
class Contexto:
    """Tudo que as tools precisam saber sobre quem está falando."""

    espaco: object
    usuario: object = None
    hoje: date = field(default_factory=timezone.localdate)
    origem: str = Origem.TEXTO


@dataclass
class Resposta:
    texto: str
    ferramentas_usadas: list[str] = field(default_factory=list)
    iteracoes: int = 0


def responder(
    contexto: Contexto,
    conteudo,
    historico: list | None = None,
    cliente=None,
) -> Resposta:
    """Uma rodada de conversa.

    `conteudo` é str ou uma lista de blocos (para imagem e PDF, que a Claude lê
    nativamente — áudio não, por isso ele chega aqui já transcrito).
    """
    if contexto.usuario is not None and not tem_quota(contexto.usuario):
        raise SemQuota("Quota mensal de tokens esgotada.")

    cliente = cliente or obter_cliente()
    mensagens = list(historico or [])
    mensagens.append({"role": "user", "content": conteudo})

    sistema = _montar_sistema(contexto)
    usadas: list[str] = []
    iteracao = 0

    while iteracao < settings.AI_MAX_ITERACOES:
        iteracao += 1
        esforco = (
            settings.AI_EFFORT_ANALISE if _pediu_analise(usadas) else settings.AI_EFFORT_REGISTRO
        )

        resposta = cliente.messages.create(
            model=settings.AI_MODEL,
            max_tokens=settings.AI_MAX_TOKENS,
            system=sistema,
            messages=mensagens,
            tools=tools.TOOLS,
            output_config={"effort": esforco},
        )

        if contexto.usuario is not None:
            registrar_consumo(contexto.usuario, settings.AI_MODEL, resposta.usage)

        mensagens.append({"role": "assistant", "content": resposta.content})

        if resposta.stop_reason != "tool_use":
            return Resposta(
                texto=_texto_de(resposta), ferramentas_usadas=usadas, iteracoes=iteracao
            )

        # Blocos de tool_use podem vir vários na mesma resposta, e TODOS os
        # tool_result precisam voltar numa ÚNICA mensagem de usuário —
        # espalhá-los em mensagens separadas ensina o modelo a parar de chamar
        # ferramentas em paralelo.
        resultados = []
        for bloco in resposta.content:
            if bloco.type != "tool_use":
                continue
            usadas.append(bloco.name)
            logger.info("tool %s args=%s", bloco.name, bloco.input)
            saida = tools.executar(bloco.name, dict(bloco.input), contexto)
            resultados.append(
                {
                    "type": "tool_result",
                    "tool_use_id": bloco.id,
                    "content": saida,
                    "is_error": saida.startswith("ERRO:"),
                }
            )

        mensagens.append({"role": "user", "content": resultados})

    # Teto batido. Não é para acontecer com um agente deste tamanho; se
    # acontecer, é uma tool falhando em laço — melhor cortar do que queimar a
    # quota da pessoa.
    logger.warning("Teto de %s iterações atingido.", settings.AI_MAX_ITERACOES)
    return Resposta(
        texto="Me embananei aqui 😅 Pode repetir de outro jeito?",
        ferramentas_usadas=usadas,
        iteracoes=iteracao,
    )


def _montar_sistema(contexto: Contexto) -> list[dict]:
    """Prefixo estável com o breakpoint de cache; contexto volátil depois.

    Se o `cache_control` ficasse no último bloco, a data de hoje entraria no
    prefixo e o cache seria invalidado toda meia-noite — pior, a cada espaço
    diferente.
    """
    categorias = list(
        Categoria.objects.filter(espaco=contexto.espaco, ativa=True).values_list("nome", flat=True)
    )
    contas = list(
        Conta.objects.filter(espaco=contexto.espaco, ativa=True).values_list("nome", flat=True)
    )

    return [
        {
            "type": "text",
            "text": INSTRUCOES,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": contexto_do_espaco(contexto.espaco, contexto.hoje, categorias, contas),
        },
    ]


def _pediu_analise(usadas: list[str]) -> bool:
    """Sobe o esforço quando a conversa virou pergunta analítica.

    Registrar gasto é mecânico e roda em `low`; "dá pra comprar?" precisa de
    julgamento sobre os números que a tool devolveu.
    """
    return any(nome in tools.SOMENTE_LEITURA for nome in usadas)


def _texto_de(resposta) -> str:
    partes = [bloco.text for bloco in resposta.content if bloco.type == "text"]
    return "\n".join(p for p in partes if p).strip()
