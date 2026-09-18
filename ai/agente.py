"""O agente: loop de tool use sobre a Claude.

Loop manual, e não o tool runner do SDK. Três razões:

1. As tools têm efeito colateral no banco, e aqui o controle do que roda, em
   que ordem e sob qual quota é explícito.
2. O loop é curto: uma ou duas iterações resolvem quase tudo.
3. Um loop próprio é trivial de testar com um cliente falso, que é como o resto
   da frota testa (unittest.mock puro, sem factory_boy).
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.utils import timezone

from accounts.models import Espaco, Usuario
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

    espaco: Espaco
    usuario: Usuario | None = None
    hoje: date = field(default_factory=timezone.localdate)
    origem: str = Origem.TEXTO


@dataclass
class Resposta:
    texto: str
    ferramentas_usadas: list[str] = field(default_factory=list)
    iteracoes: int = 0
    # As mensagens deste turno, prontas para virar histórico do próximo. Vão
    # com os blocos `tool_use` e `tool_result`, é isso que diz ao modelo que
    # a escrita ACONTECEU. Ver `_para_historico`.
    turno: list[dict] = field(default_factory=list)


def responder(
    contexto: Contexto,
    conteudo,
    historico: list | None = None,
    cliente=None,
) -> Resposta:
    """Uma rodada de conversa.

    `conteudo` é str ou uma lista de blocos (para imagem e PDF, que a Claude lê
    nativamente, áudio não, por isso ele chega aqui já transcrito).
    """
    if contexto.usuario is not None and not tem_quota(contexto.usuario):
        raise SemQuota("Quota mensal de tokens esgotada.")

    cliente = cliente or obter_cliente()
    mensagens = list(historico or [])
    # Onde começa ESTE turno: o que vier daqui para frente é o que o próximo
    # turno precisa reproduzir.
    inicio = len(mensagens)
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
                texto=_texto_de(resposta),
                ferramentas_usadas=usadas,
                iteracoes=iteracao,
                turno=_para_historico(mensagens[inicio + 1 :]),
            )

        # Blocos de tool_use podem vir vários na mesma resposta, e TODOS os
        # tool_result precisam voltar numa ÚNICA mensagem de usuário,
        # espalhá-los em mensagens separadas ensina o modelo a parar de chamar
        # ferramentas em paralelo.
        mensagens.append({"role": "user", "content": _executar_tools(resposta, contexto, usadas)})

    # Teto batido. Não é para acontecer com um agente deste tamanho; se
    # acontecer, é uma tool falhando em laço, melhor cortar do que queimar a
    # quota da pessoa.
    logger.warning("Teto de %s iterações atingido.", settings.AI_MAX_ITERACOES)
    return Resposta(
        texto="Me embananei aqui 😅 Pode repetir de outro jeito?",
        ferramentas_usadas=usadas,
        iteracoes=iteracao,
        turno=_para_historico(mensagens[inicio + 1 :]),
    )


# Blocos que valem a pena guardar. `thinking` fica de fora: é caro, é longo e
# a API não o exige de volta em turnos anteriores. `tool_use` é o que importa.
_BLOCOS_NO_HISTORICO = {"text", "tool_use"}

# Mídia não volta: remandar a imagem de todo turno anterior multiplicaria o
# custo por nada, o que importava dela já virou lançamento.
_SEM_MIDIA = "(mídia enviada neste turno)"


def _para_historico(mensagens: list[dict]) -> list[dict]:
    """Serializa a troca do agente para o banco, em JSON puro.

    Guardar `tool_use` e `tool_result` é o ponto todo. Um histórico só de
    texto faz o modelo ler a própria confirmação (“Uber de R$ 20 registrado”)
    como narração, não como prova de que a escrita aconteceu, e refazer. Foi
    o que duplicou um lançamento em produção.

    A fala do usuário fica DE FORA: ela já é a Mensagem de entrada no banco.
    Guardá-la aqui também obrigaria quem monta o histórico a descobrir qual
    entrada já está coberta por qual turno, e errar isso perde uma fala.
    """
    saida: list[dict] = []

    for mensagem in mensagens:
        conteudo = _conteudo_serializavel(mensagem["content"])
        # Um turno em que o modelo só pensou e chamou tool não tem bloco algum
        # que valha guardar; mensagem de conteúdo vazio é recusada pela API.
        if not conteudo:
            continue
        saida.append({"role": mensagem["role"], "content": conteudo})

    return _sem_tool_use_orfao(saida)


def _conteudo_serializavel(conteudo):
    if isinstance(conteudo, str):
        return conteudo

    blocos = []
    for bloco in conteudo:
        dados = _como_dicionario(bloco)
        tipo = dados.get("type")

        if tipo in ("image", "document"):
            blocos.append({"type": "text", "text": _SEM_MIDIA})
        elif tipo == "tool_result" or tipo in _BLOCOS_NO_HISTORICO:
            blocos.append(dados)

    return blocos


def _executar_tools(resposta, contexto: Contexto, usadas: list[str]) -> list[dict]:
    """Roda as ferramentas pedidas e devolve os `tool_result` correspondentes.

    Devolve uma lista porque TODOS os resultados precisam voltar numa ÚNICA
    mensagem de usuário: espalhá-los em mensagens separadas ensina o modelo a
    parar de chamar ferramentas em paralelo.

    `usadas` é alimentada aqui, e não devolvida, porque quem chama precisa da
    lista acumulada ao longo de todas as iteracões, não só desta.
    """
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
    return resultados


def _como_dicionario(bloco) -> dict:
    """Bloco em JSON puro, venha de onde vier.

    Três formas chegam aqui: os `tool_result` que nós mesmos montamos (já são
    dicionários), os blocos do SDK (pydantic) e os do cliente falso da suíte
    (dataclasses). Depender só de `model_dump` quebraria os testes sem quebrar
    a produção, que é a pior combinação possível.
    """
    if isinstance(bloco, dict):
        return bloco
    if hasattr(bloco, "model_dump"):
        return bloco.model_dump(exclude_none=True)
    if dataclasses.is_dataclass(bloco) and not isinstance(bloco, type):
        return dataclasses.asdict(bloco)
    return {k: v for k, v in vars(bloco).items() if not k.startswith("_")}


def _sem_tool_use_orfao(mensagens: list[dict]) -> list[dict]:
    """Corta um `tool_use` final que ficou sem o `tool_result` correspondente.

    Acontece quando o teto de iterações corta o laço no meio: a API recusa o
    histórico com 400 se um `tool_use` não for seguido do resultado dele.
    """
    while mensagens:
        ultima = mensagens[-1]
        blocos = ultima["content"]
        tem_tool_use = isinstance(blocos, list) and any(b.get("type") == "tool_use" for b in blocos)
        if ultima["role"] == "assistant" and tem_tool_use:
            mensagens.pop()
            continue
        return mensagens
    return mensagens


def _montar_sistema(contexto: Contexto) -> list[dict]:
    """Prefixo estável com o breakpoint de cache; contexto volátil depois.

    Se o `cache_control` ficasse no último bloco, a data de hoje entraria no
    prefixo e o cache seria invalidado toda meia-noite, pior, a cada espaço
    diferente.
    """
    categorias = list(
        Categoria.objects.filter(espaco=contexto.espaco, ativa=True).values_list("nome", flat=True)
    )
    contas = list(
        Conta.objects.filter(espaco=contexto.espaco, ativa=True).values_list("nome", flat=True)
    )

    outros = []
    if contexto.usuario is not None:
        outros = [
            m.get_short_name() or m.username
            for m in contexto.espaco.membros.exclude(pk=contexto.usuario.pk)
        ]

    return [
        {
            "type": "text",
            "text": INSTRUCOES,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": contexto_do_espaco(contexto.espaco, contexto.hoje, categorias, contas, outros),
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
