"""Canais de mensagem.

`obter_canal()` é o único ponto que decide qual implementação atende. Todo o
resto do código fala com a interface, o que é o que permite o console web usar
exatamente o mesmo agente que o WhatsApp.
"""

from django.conf import settings

from .base import CanalMensagem, MensagemEnviada
from .console import ConsoleCanal
from .fake import FakeCanal

_CANAIS = {
    "console": ConsoleCanal,
    "fake": FakeCanal,
}


def obter_canal(nome: str | None = None) -> CanalMensagem:
    nome = nome or settings.CANAL_PADRAO
    if nome == "cloud_api":
        # Importado tarde: o módulo lê credenciais e não deve ser carregado em
        # dev nem na suíte, onde elas não existem.
        from .cloud_api import CloudAPICanal

        return CloudAPICanal()
    try:
        return _CANAIS[nome]()
    except KeyError as exc:
        raise ValueError(f"Canal desconhecido: {nome}") from exc


__all__ = ["CanalMensagem", "ConsoleCanal", "FakeCanal", "MensagemEnviada", "obter_canal"]
