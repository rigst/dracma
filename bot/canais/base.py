"""Interface comum a todos os canais."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MensagemEnviada:
    """O que um canal devolve depois de entregar."""

    id_externo: str = ""
    entregue: bool = True
    erro: str = ""
    # O destino recusou de forma permanente — no Telegram, 403: a pessoa
    # bloqueou o bot ou apagou a conversa. Distinto de `entregue=False` por
    # falha de rede, que adianta tentar de novo. Aqui não adianta, e quem
    # chama marca a conta para parar de insistir.
    bloqueado: bool = False


@dataclass
class MidiaBaixada:
    conteudo: bytes
    mime_type: str
    tamanho: int


class CanalMensagem:
    """Transporte de mensagens.

    Existem três implementações: `telegram` fala com a Bot API, `console`
    desenha no portal (é o que sustenta a demo pública e o desenvolvimento sem
    túnel HTTPS) e `fake` guarda em memória para os testes.
    """

    nome = "base"

    def enviar_texto(self, destino, texto: str) -> MensagemEnviada:
        raise NotImplementedError

    def baixar_midia(self, file_id: str, mime_hint: str = "") -> MidiaBaixada:
        raise NotImplementedError
