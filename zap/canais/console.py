"""Canal do console web.

Grava a mensagem no banco e o portal a desenha. Paga duas dívidas de uma vez:
desenvolver sem ngrok e sem credencial da Meta, e entregar uma demo pública em
que qualquer pessoa usa o produto inteiro no navegador — sem precisar estar na
allowlist de 5 números do número de teste da Meta.

Não tem restrição de janela de 24h: a limitação é da plataforma da Meta, e o
`enviar_template` herdado da base cai no texto.
"""

from __future__ import annotations

from .base import CanalMensagem, MensagemEnviada, MidiaBaixada


class ConsoleCanal(CanalMensagem):
    nome = "console"

    def enviar_texto(self, destino, texto: str) -> MensagemEnviada:
        # A persistência é feita por quem chama (zap.janela), que já grava a
        # Mensagem de saída. Aqui não há transporte: o portal lê do banco.
        return MensagemEnviada(id_externo="")

    def baixar_midia(self, media_id: str) -> MidiaBaixada:
        # No console a mídia sobe pelo formulário e já chega como arquivo; não
        # há nada para buscar numa API externa.
        raise NotImplementedError("O canal console recebe a mídia pelo upload do formulário.")

    def suporta_template(self) -> bool:
        return False
