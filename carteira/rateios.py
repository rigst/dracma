"""Dividir um gasto entre as pessoas do espaço.

Três jeitos de dizer a mesma coisa — igual, por proporção e por valor — e um
resultado só: quanto cabe a cada um, em reais. A conversão acontece na entrada,
e não a cada leitura, porque o arredondamento precisa ser decidido UMA vez: a
soma das partes tem que bater com o total ao centavo, sempre.

R$ 10,00 entre três pessoas não dá 3,33 para cada — dá 3,34, 3,33 e 3,33. Os
centavos que sobram vão para os primeiros da fila, e a ordem é estável (pelo id
da pessoa), para a mesma divisão dar sempre o mesmo resultado.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

CENTAVO = Decimal("0.01")


class ErroDeRateio(Exception):
    """A mensagem vai para a tela como está."""


def dividir_igual(valor: Decimal, pessoas) -> dict:
    """Partes iguais, com os centavos restantes nos primeiros."""
    pessoas = list(pessoas)
    if not pessoas:
        raise ErroDeRateio("Não há ninguém para dividir.")
    return _distribuir(valor, [Decimal(1)] * len(pessoas), pessoas)


def dividir_por_percentual(valor: Decimal, percentuais: dict) -> dict:
    """`percentuais` é {pessoa: Decimal}. Precisa somar 100."""
    if not percentuais:
        raise ErroDeRateio("Informe a porcentagem de cada pessoa.")

    total = sum(percentuais.values())
    if abs(total - Decimal(100)) > Decimal("0.01"):
        raise ErroDeRateio(f"As porcentagens somam {total}%, e precisam somar 100%.")

    pessoas = sorted(percentuais, key=lambda p: p.pk)
    return _distribuir(valor, [percentuais[p] for p in pessoas], pessoas)


def dividir_por_valor(valor: Decimal, valores: dict) -> dict:
    """`valores` é {pessoa: Decimal}. Precisa somar o valor do lançamento."""
    if not valores:
        raise ErroDeRateio("Informe quanto cabe a cada pessoa.")

    total = sum(valores.values())
    if total != valor:
        raise ErroDeRateio(f"As partes somam R$ {total:.2f} e o lançamento é de R$ {valor:.2f}.")
    return {pessoa: parte for pessoa, parte in valores.items() if parte > 0}


def _distribuir(valor: Decimal, pesos, pessoas) -> dict:
    """Reparte `valor` na proporção dos pesos, sem perder nem inventar centavo.

    Arredonda cada parte para BAIXO e depois devolve o que sobrou, um centavo
    por vez. Arredondar normalmente pode gerar uma soma maior que o total — e
    aí o relatório mostraria um gasto que não existiu.
    """
    pessoas = list(pessoas)
    total_pesos = sum(pesos)
    if total_pesos <= 0:
        raise ErroDeRateio("A divisão precisa de pelo menos uma parte maior que zero.")

    partes = [
        (valor * Decimal(peso) / Decimal(total_pesos)).quantize(CENTAVO, rounding=ROUND_DOWN)
        for peso in pesos
    ]

    sobra = valor - sum(partes)
    indice = 0
    while sobra >= CENTAVO and partes:
        partes[indice % len(partes)] += CENTAVO
        sobra -= CENTAVO
        indice += 1

    return {pessoa: parte for pessoa, parte in zip(pessoas, partes, strict=True) if parte > 0}


# ---------------------------------------------------------------------------
# Padrão do espaço
# ---------------------------------------------------------------------------


def padrao_do_espaco(espaco) -> dict | None:
    """Percentuais configurados, ou None quando a divisão é igual."""
    from .models import RateioPadrao

    linhas = list(
        RateioPadrao.objects.filter(espaco=espaco).select_related("pessoa").order_by("pessoa__pk")
    )
    if not linhas:
        return None
    return {linha.pessoa: linha.percentual for linha in linhas}


def definir_padrao(espaco, percentuais: dict | None) -> None:
    """`None` volta para a divisão igual."""
    from .models import RateioPadrao

    RateioPadrao.objects.filter(espaco=espaco).delete()
    if not percentuais:
        return

    total = sum(percentuais.values())
    if abs(total - Decimal(100)) > Decimal("0.01"):
        raise ErroDeRateio(f"As porcentagens somam {total}%, e precisam somar 100%.")

    RateioPadrao.objects.bulk_create(
        [
            RateioPadrao(espaco=espaco, pessoa=pessoa, percentual=percentual)
            for pessoa, percentual in percentuais.items()
        ]
    )


# ---------------------------------------------------------------------------
# Aplicar a um lançamento
# ---------------------------------------------------------------------------


def aplicar(transacao, modo: str = "padrao", partes: dict | None = None):
    """Grava o rateio de um lançamento. Devolve {pessoa: valor}.

    Um lançamento PESSOAL não tem rateio: é inteiro de quem lançou, e gravar
    linhas para ele só criaria estado para manter em sincronia.
    """
    from .models import Rateio

    Rateio.objects.filter(transacao=transacao).delete()
    if not transacao.compartilhada:
        return {}

    membros = list(transacao.espaco.membros.order_by("pk"))
    if not membros:
        return {}

    if modo == "padrao":
        percentuais = padrao_do_espaco(transacao.espaco)
        if percentuais:
            # O padrão pode ter sido configurado antes de alguém entrar ou sair.
            # Quem não está mais no espaço perde a parte, e o que sobra é
            # repartido mantendo a PROPORÇÃO entre quem ficou — 70/20/10 sem o
            # terceiro vira 77,78/22,22, e não um erro de "não soma 100%".
            restantes = {p: v for p, v in percentuais.items() if p in membros}
            # Quem entrou depois do padrão ainda não tem parte; sem isto ficaria
            # de fora de todo lançamento em vez de dividir.
            novos = [p for p in membros if p not in restantes]
            if novos or not restantes:
                divisao = dividir_igual(transacao.valor, membros)
            else:
                pessoas = sorted(restantes, key=lambda p: p.pk)
                divisao = _distribuir(transacao.valor, [restantes[p] for p in pessoas], pessoas)
        else:
            divisao = dividir_igual(transacao.valor, membros)
    elif modo == "igual":
        divisao = dividir_igual(transacao.valor, membros)
    elif modo == "percentual":
        divisao = dividir_por_percentual(transacao.valor, partes or {})
    elif modo == "valor":
        divisao = dividir_por_valor(transacao.valor, partes or {})
    else:
        raise ErroDeRateio(f"Modo de divisão desconhecido: {modo}.")

    Rateio.objects.bulk_create(
        [
            Rateio(transacao=transacao, pessoa=pessoa, valor=valor)
            for pessoa, valor in divisao.items()
        ]
    )
    return divisao
