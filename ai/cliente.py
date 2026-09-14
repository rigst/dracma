"""Cliente da Anthropic.

Isolado num módulo próprio para que o agente possa receber um cliente falso nos
testes sem precisar de mock de import.
"""

from __future__ import annotations

from functools import lru_cache

from django.conf import settings


@lru_cache(maxsize=1)
def obter_cliente():
    """Instância única. O SDK já faz pool de conexões e retry (2 tentativas em
    429, 5xx e falha de conexão); criar um cliente por mensagem joga fora o
    pool e multiplica handshake TLS."""
    import anthropic

    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)
