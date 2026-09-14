"""Limitação de rajada em contador de cache.

Usado onde o custo de não limitar é dinheiro: criação de visitante (um script
criaria contas em massa, cada uma com quota de IA própria), cadastro e
mensagens ao agente.

O `ip_do_request` NÃO mora aqui: já existe em `legal.utils`, com o cuidado de
ler o último valor do X-Forwarded-For e não o primeiro.
"""

from __future__ import annotations

from django.core.cache import cache


def excedeu_limite(chave: str, limite: int, janela_s: int) -> bool:
    """Contador com expiração. `add` só cria se não existir, o que inicia a
    janela na primeira chamada e a mantém fixa até expirar."""
    chave_cache = f"rajada:{chave}"
    cache.add(chave_cache, 0, janela_s)
    try:
        atual = cache.incr(chave_cache)
    except ValueError:
        # A chave expirou entre o add e o incr.
        cache.set(chave_cache, 1, janela_s)
        atual = 1
    return atual > limite
