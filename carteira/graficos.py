"""Gráficos em SVG, gerados no servidor.

Sem Chart.js e sem nenhuma lib: o SVG sai pronto do template, funciona com o
JavaScript desligado, não adiciona requisição nem dependência de CDN, e as
cores saem dos mesmos tokens CSS do resto da página — então o tema escuro
funciona de graça.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

# Paleta categórica: matizes distintos e com luminosidade parecida, para
# nenhuma fatia sumir no tema escuro nem gritar no claro.
CORES = [
    "#8b5cf6",
    "#0ea5e9",
    "#10b981",
    "#f59e0b",
    "#ec4899",
    "#14b8a6",
    "#f43f5e",
    "#a3a3a3",
]


@dataclass
class Fatia:
    rotulo: str
    valor: Decimal
    percentual: int
    cor: str
    caminho: str = ""
    largura: int = 0


def rosca(itens, raio: int = 80, espessura: int = 28) -> list[Fatia]:
    """Fatias de um donut. `itens` é [(rótulo, valor)] já ordenado.

    Devolve os caminhos prontos; o template só desenha. Mantém no máximo 7
    categorias e agrupa o resto em "Outras" — acima disso as fatias ficam
    finas demais para serem lidas, e a legenda vira uma lista.
    """
    itens = [(rotulo, Decimal(valor)) for rotulo, valor in itens if Decimal(valor) > 0]
    if not itens:
        return []

    if len(itens) > 7:
        principais = itens[:7]
        resto = sum(valor for _, valor in itens[7:])
        itens = [*principais, ("Outras", resto)]

    total = sum(valor for _, valor in itens)
    if total <= 0:
        return []

    fatias: list[Fatia] = []
    angulo = -math.pi / 2  # começa no topo

    for indice, (rotulo, valor) in enumerate(itens):
        proporcao = float(valor / total)
        # Um traço de arco com 100% vira um caminho degenerado (começo e fim no
        # mesmo ponto) e não desenha nada: corta em 99,99%.
        varredura = min(proporcao, 0.9999) * 2 * math.pi
        fim = angulo + varredura

        fatias.append(
            Fatia(
                rotulo=rotulo,
                valor=valor,
                percentual=round(proporcao * 100),
                cor=CORES[indice % len(CORES)],
                caminho=_arco(raio, espessura, angulo, fim),
            )
        )
        angulo = fim

    return fatias


def _arco(raio: int, espessura: int, inicio: float, fim: float) -> str:
    interno = raio - espessura
    grande = 1 if (fim - inicio) > math.pi else 0

    x1, y1 = _ponto(raio, inicio)
    x2, y2 = _ponto(raio, fim)
    x3, y3 = _ponto(interno, fim)
    x4, y4 = _ponto(interno, inicio)

    return (
        f"M {x1:.2f} {y1:.2f} "
        f"A {raio} {raio} 0 {grande} 1 {x2:.2f} {y2:.2f} "
        f"L {x3:.2f} {y3:.2f} "
        f"A {interno} {interno} 0 {grande} 0 {x4:.2f} {y4:.2f} Z"
    )


def _ponto(raio: int, angulo: float) -> tuple[float, float]:
    return raio * math.cos(angulo), raio * math.sin(angulo)


def barras(itens, largura_max: int = 100) -> list[Fatia]:
    """Barras horizontais proporcionais ao MAIOR valor, não ao total.

    Comparar entre si é o que interessa aqui; a proporção sobre o total já é o
    trabalho da rosca.
    """
    itens = [(rotulo, Decimal(valor)) for rotulo, valor in itens if Decimal(valor) > 0]
    if not itens:
        return []

    maior = max(valor for _, valor in itens)
    total = sum(valor for _, valor in itens)

    return [
        Fatia(
            rotulo=rotulo,
            valor=valor,
            percentual=round(float(valor / total) * 100),
            cor=CORES[indice % len(CORES)],
            largura=max(2, round(float(valor / maior) * largura_max)),
        )
        for indice, (rotulo, valor) in enumerate(itens)
    ]
