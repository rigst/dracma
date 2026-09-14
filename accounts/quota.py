"""Quota mensal de tokens de IA.

A demo desta aplicação é pública e o visitante é anônimo: sem um teto por
usuário, uma visita insistente queima a conta da API. O balde é mensal e
reinicia no dia 1.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone


def _inicio_do_mes():
    hoje = timezone.now()
    return hoje.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def tokens_consumidos(usuario) -> int:
    from .models import ConsumoIA

    agregado = ConsumoIA.objects.filter(
        usuario=usuario, criado_em__gte=_inicio_do_mes()
    ).aggregate(
        entrada=Sum("tokens_entrada"),
        saida=Sum("tokens_saida"),
        cache_w=Sum("tokens_cache_escrita"),
        cache_r=Sum("tokens_cache_leitura"),
    )
    return sum(valor or 0 for valor in agregado.values())


def tokens_restantes(usuario) -> int:
    return max(0, usuario.quota_tokens - tokens_consumidos(usuario))


def tem_quota(usuario) -> bool:
    return tokens_restantes(usuario) > 0


def custo_usd(modelo: str, entrada: int, saida: int, cache_escrita: int, cache_leitura: int):
    """Custo de uma chamada, em USD.

    Cache não é cobrado como entrada normal: escrita custa ~1,25x e leitura
    ~0,1x. Somar tudo como entrada superestima justamente na conversa longa,
    que é onde o cache trabalha.
    """
    preco_in, preco_out = settings.AI_PRICES.get(
        modelo,
        (settings.AI_PRICE_INPUT_PER_MTOK, settings.AI_PRICE_OUTPUT_PER_MTOK),
    )
    milhao = Decimal("1000000")
    total = (
        Decimal(entrada) * Decimal(str(preco_in))
        + Decimal(saida) * Decimal(str(preco_out))
        + Decimal(cache_escrita) * Decimal(str(preco_in)) * Decimal("1.25")
        + Decimal(cache_leitura) * Decimal(str(preco_in)) * Decimal("0.1")
    ) / milhao
    return total.quantize(Decimal("0.000001"))


def registrar_consumo(usuario, modelo: str, usage) -> None:
    """Grava o `usage` de uma resposta da Anthropic."""
    from .models import ConsumoIA

    entrada = getattr(usage, "input_tokens", 0) or 0
    saida = getattr(usage, "output_tokens", 0) or 0
    cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0

    ConsumoIA.objects.create(
        usuario=usuario,
        modelo=modelo,
        tokens_entrada=entrada,
        tokens_saida=saida,
        tokens_cache_escrita=cache_w,
        tokens_cache_leitura=cache_r,
        custo_usd=custo_usd(modelo, entrada, saida, cache_w, cache_r),
    )
