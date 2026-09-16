"""Canal de teste: acumula em memória, não sai da máquina."""

from __future__ import annotations

from .base import CanalMensagem, MensagemEnviada, MidiaBaixada


class FakeCanal(CanalMensagem):
    nome = "fake"

    def __init__(self):
        self.enviadas: list[dict] = []
        self.midias: dict[str, MidiaBaixada] = {}
        self.falhar = False
        self.bloquear = False

    def enviar_texto(self, destino, texto: str) -> MensagemEnviada:
        if self.bloquear:
            return MensagemEnviada(
                entregue=False, erro="Forbidden: bot was blocked by the user", bloqueado=True
            )
        if self.falhar:
            return MensagemEnviada(entregue=False, erro="falha simulada")
        self.enviadas.append({"destino": str(destino), "tipo": "texto", "texto": texto})
        return MensagemEnviada(id_externo=f"fake-{len(self.enviadas)}")

    def baixar_midia(self, file_id: str, mime_hint: str = "") -> MidiaBaixada:
        return self.midias[file_id]

    # -- auxiliares de teste ------------------------------------------------

    @property
    def ultimo_texto(self) -> str:
        return self.enviadas[-1]["texto"] if self.enviadas else ""

    def limpar(self):
        self.enviadas.clear()
