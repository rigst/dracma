"""
Usuário, Espaço financeiro e quota de IA.

O `Espaco` é a unidade de colaboração: toda transação pertence a um espaço, não
a uma pessoa. É o que entrega "usuários colaborativos ilimitados" (casal,
família, time) sem tabela de compartilhamento nem duplicação de lançamento.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

# Alfabeto do código de convite: sem 0/O e 1/I/L, que a pessoa erra ao digitar
# do que leu na tela.
ALFABETO_CODIGO = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def gerar_codigo(tamanho: int = 6) -> str:
    """Código curto e legível. `secrets` e não `random`: além de ser um código
    de acesso de verdade, o `random` vira VULNERABILITY no Sonar e derruba o
    gate por new_security_rating."""
    return "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(tamanho))


class Espaco(models.Model):
    """Onde o dinheiro é acompanhado em conjunto."""

    nome = models.CharField("nome", max_length=80, default="Meu espaço")
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "espaço"
        verbose_name_plural = "espaços"

    def __str__(self) -> str:
        return self.nome


class Usuario(AbstractUser):
    """Usuário do portal e do Telegram.

    O modelo é customizado desde a primeira migration: trocar `AUTH_USER_MODEL`
    depois do primeiro migrate exige cirurgia no banco.
    """

    email = models.EmailField("e-mail", unique=True)
    espaco = models.ForeignKey(
        Espaco,
        on_delete=models.CASCADE,
        related_name="membros",
        null=True,
        blank=True,
        verbose_name="espaço",
    )
    # Visitante é conta descartável criada pela demo pública; expira por
    # inatividade e tem quota de IA própria.
    is_visitante = models.BooleanField("é visitante", default=False)
    ultimo_acesso = models.DateTimeField("último acesso", default=timezone.now)

    class Meta(AbstractUser.Meta):
        verbose_name = "usuário"
        verbose_name_plural = "usuários"

    def __str__(self) -> str:
        return self.get_full_name() or self.username

    @property
    def quota_tokens(self) -> int:
        if self.is_visitante:
            return settings.QUOTA_TOKENS_VISITOR
        return settings.QUOTA_TOKENS_DEFAULT

    def expirou(self) -> bool:
        """Visitante parado há mais que VISITOR_EXPIRY_HOURS."""
        if not self.is_visitante:
            return False
        limite = timezone.now() - timedelta(hours=settings.VISITOR_EXPIRY_HOURS)
        return self.ultimo_acesso < limite


class ConviteEspaco(models.Model):
    """Convite por código para alguém entrar no espaço.

    É o que torna a colaboração real: a pessoa recebe um código de 6 caracteres
    e entra no mesmo mês financeiro, sem criar um espaço paralelo.
    """

    espaco = models.ForeignKey(Espaco, on_delete=models.CASCADE, related_name="convites")
    codigo = models.CharField("código", max_length=12, unique=True, default=gerar_codigo)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="convites_criados"
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    usado_em = models.DateTimeField("usado em", null=True, blank=True)
    expira_em = models.DateTimeField("expira em")

    class Meta:
        verbose_name = "convite"
        verbose_name_plural = "convites"

    def __str__(self) -> str:
        return f"{self.codigo} → {self.espaco}"

    def valido(self) -> bool:
        return self.usado_em is None and timezone.now() < self.expira_em


class ConsumoIA(models.Model):
    """Uma chamada à API da Anthropic.

    Serve a dois propósitos: alimentar a quota mensal (sem ela, uma visita
    insistente na demo pública queima a conta) e render o painel de custo.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="consumos_ia"
    )
    modelo = models.CharField("modelo", max_length=60)
    tokens_entrada = models.PositiveIntegerField("tokens de entrada", default=0)
    tokens_saida = models.PositiveIntegerField("tokens de saída", default=0)
    # Separados dos tokens de entrada porque custam diferente: escrita ~1,25x e
    # leitura ~0,1x. Somar tudo como entrada superestima o custo em conversa
    # longa, que é justamente onde o cache trabalha.
    tokens_cache_escrita = models.PositiveIntegerField("tokens gravados em cache", default=0)
    tokens_cache_leitura = models.PositiveIntegerField("tokens lidos do cache", default=0)
    custo_usd = models.DecimalField("custo (USD)", max_digits=10, decimal_places=6, default=0)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "consumo de IA"
        verbose_name_plural = "consumos de IA"
        indexes = [models.Index(fields=["usuario", "criado_em"])]

    def __str__(self) -> str:
        return f"{self.usuario} · {self.modelo} · {self.total_tokens} tokens"

    @property
    def total_tokens(self) -> int:
        return (
            self.tokens_entrada
            + self.tokens_saida
            + self.tokens_cache_escrita
            + self.tokens_cache_leitura
        )
