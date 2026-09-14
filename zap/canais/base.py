"""Interface comum a todos os canais."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MensagemEnviada:
    """O que um canal devolve depois de entregar."""

    id_externo: str = ""
    entregue: bool = True
    erro: str = ""


@dataclass
class MidiaBaixada:
    conteudo: bytes
    mime_type: str
    tamanho: int


class CanalMensagem:
    """Transporte de mensagens.

    Existem três implementações: `cloud_api` fala com a Meta, `console` desenha
    no portal (é o que sustenta a demo pública e o desenvolvimento sem ngrok) e
    `fake` guarda em memória para os testes.
    """

    nome = "base"

    def enviar_texto(self, destino, texto: str) -> MensagemEnviada:
        raise NotImplementedError

    def enviar_template(self, destino, template: str, parametros: list[str]) -> MensagemEnviada:
        """Usado quando a janela de 24h da Meta está fechada.

        Nos canais que não têm essa restrição (console, fake) a implementação
        padrão cai no texto: a regra é da plataforma da Meta, não do domínio.
        """
        return self.enviar_texto(destino, " ".join(parametros))

    def baixar_midia(self, media_id: str) -> MidiaBaixada:
        raise NotImplementedError

    def suporta_template(self) -> bool:
        return False
