"""Cliente falso da Anthropic, para testar o agente sem gastar dinheiro.

Reproduz só o que o agente usa: `messages.create` devolvendo blocos com
`.type`, `.name`, `.input`, `.id`, `.text`, mais `stop_reason` e `usage`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BlocoTexto:
    text: str
    type: str = "text"


@dataclass
class BlocoTool:
    name: str
    input: dict
    id: str = "toolu_teste"
    type: str = "tool_use"


@dataclass
class Uso:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class RespostaFalsa:
    content: list
    stop_reason: str = "end_turn"
    usage: Uso = field(default_factory=Uso)


class MensagensFalsas:
    def __init__(self, dono):
        self._dono = dono

    def create(self, **kwargs):
        # Cópia da lista de mensagens: o agente continua acrescentando turnos
        # nela DEPOIS desta chamada, e guardar a referência faria as asserções
        # descreverem o estado final, não o que foi de fato enviado.
        registro = dict(kwargs)
        registro["messages"] = list(kwargs.get("messages") or [])
        self._dono.chamadas.append(registro)
        if not self._dono.respostas:
            raise AssertionError("O cliente falso ficou sem respostas programadas.")
        return self._dono.respostas.pop(0)


class ClienteFalso:
    """Devolve, em ordem, as respostas com que foi programado."""

    def __init__(self, respostas: list[RespostaFalsa] | None = None):
        self.respostas: list[RespostaFalsa] = list(respostas or [])
        self.chamadas: list[dict[str, Any]] = []
        self.messages = MensagensFalsas(self)

    # -- açúcar para montar cenários ---------------------------------------

    def responde(self, texto: str) -> ClienteFalso:
        self.respostas.append(RespostaFalsa(content=[BlocoTexto(texto)]))
        return self

    def chama(self, nome: str, **argumentos) -> ClienteFalso:
        self.respostas.append(
            RespostaFalsa(
                content=[BlocoTool(name=nome, input=argumentos, id=f"toolu_{len(self.respostas)}")],
                stop_reason="tool_use",
            )
        )
        return self

    def chama_varias(self, *pedidos) -> ClienteFalso:
        """Várias tools numa resposta só, como a API faz de verdade."""
        blocos = [
            BlocoTool(name=nome, input=args, id=f"toolu_{i}")
            for i, (nome, args) in enumerate(pedidos)
        ]
        self.respostas.append(RespostaFalsa(content=blocos, stop_reason="tool_use"))
        return self

    # -- inspeção -----------------------------------------------------------

    @property
    def ultima_chamada(self) -> dict:
        return self.chamadas[-1]

    @property
    def ultimo_conteudo_do_usuario(self):
        """O `content` da última mensagem de papel `user` enviada.

        Não serve `messages[-1]`: depois de uma rodada de tool use, a última
        mensagem é a dos tool_result.
        """
        for mensagem in reversed(self.ultima_chamada["messages"]):
            if mensagem.get("role") == "user":
                conteudo = mensagem.get("content")
                if isinstance(conteudo, list) and conteudo and isinstance(conteudo[0], dict):
                    if conteudo[0].get("type") == "tool_result":
                        continue
                return conteudo
        return None

    def resultados_enviados(self) -> list[dict]:
        """Os blocos tool_result que o agente devolveu na última chamada."""
        mensagens = self.ultima_chamada["messages"]
        for mensagem in reversed(mensagens):
            conteudo = mensagem.get("content")
            if isinstance(conteudo, list) and conteudo and isinstance(conteudo[0], dict):
                if conteudo[0].get("type") == "tool_result":
                    return conteudo
        return []
