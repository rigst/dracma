"""Criação de contas de visitante.

O modo visitante é o que abre a demo pública: o número de teste da Meta só
atende 5 destinatários allowlistados, então sem isto o projeto não seria
demonstrável por ninguém de fora.

A conta é descartável, expira por inatividade e some com tudo que é dela,
como a política de privacidade promete.
"""

from __future__ import annotations

import secrets

from django.db import transaction
from django.utils import timezone

from carteira.models import Conta, TipoConta
from carteira.seeds import semear_categorias

from .models import Espaco, Usuario

ALFABETO = "abcdefghijkmnpqrstuvwxyz23456789"


def _sufixo(tamanho: int = 8) -> str:
    return "".join(secrets.choice(ALFABETO) for _ in range(tamanho))


@transaction.atomic
def criar_visitante() -> tuple[Usuario, str]:
    """Visitante com espaço próprio, categorias e uma conta, já pronto para usar.

    Nascer com categorias e uma conta não é enfeite: sem elas, a primeira
    mensagem do visitante cairia em "sem categoria" e ele veria um produto pior
    do que o real.
    """
    marca = _sufixo()
    senha = secrets.token_urlsafe(24)

    espaco = Espaco.objects.create(nome="Espaço de demonstração")
    usuario = Usuario.objects.create_user(
        username=f"visitante_{marca}",
        email=f"visitante_{marca}@exemplo.invalid",
        password=senha,
        first_name="Visitante",
        is_visitante=True,
        espaco=espaco,
        ultimo_acesso=timezone.now(),
    )

    semear_categorias(espaco)
    Conta.objects.create(espaco=espaco, nome="Conta principal", tipo=TipoConta.CORRENTE)

    return usuario, senha
