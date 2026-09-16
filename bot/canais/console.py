"""Canal do console web.

Grava a mensagem no banco e o portal a desenha. Paga duas dívidas de uma vez:
desenvolver sem túnel HTTPS e sem bot registrado, e entregar uma demo pública
em que qualquer pessoa usa o produto inteiro no navegador, sem instalar nada.
"""

from __future__ import annotations

from .base import CanalMensagem, MensagemEnviada, MidiaBaixada


class ConsoleCanal(CanalMensagem):
    nome = "console"

    def enviar_texto(self, destino, texto: str) -> MensagemEnviada:
        # A persistência é feita por quem chama (bot.envio), que já grava a
        # Mensagem de saída. Aqui não há transporte: o portal lê do banco.
        return MensagemEnviada(id_externo="")

    def baixar_midia(self, file_id: str, mime_hint: str = "") -> MidiaBaixada:
        # No console a mídia sobe pelo formulário e já chega como arquivo; não
        # há nada para buscar numa API externa.
        raise NotImplementedError("O canal console recebe a mídia pelo upload do formulário.")
