"""Telas do portal.

Elas leem pelos mesmos serviços que o agente usa — não há uma segunda regra de
negócio aqui.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from . import graficos, services
from .models import Conta, Limite, TipoTransacao, Transacao


def _espaco(request):
    return request.user.espaco


def _periodo(request) -> tuple[date, date]:
    """Período dos filtros. Padrão: mês corrente."""
    hoje = timezone.localdate()
    inicio, fim = services.limites_do_mes(hoje)

    atalho = request.GET.get("periodo", "mes")
    if atalho == "3m":
        inicio = (hoje.replace(day=1) - timedelta(days=62)).replace(day=1)
        fim = hoje
    elif atalho == "6m":
        inicio = (hoje.replace(day=1) - timedelta(days=155)).replace(day=1)
        fim = hoje
    elif atalho == "custom":
        try:
            inicio = date.fromisoformat(request.GET["inicio"])
            fim = date.fromisoformat(request.GET["fim"])
        except (KeyError, ValueError):
            pass
    return inicio, fim


@login_required
def painel(request):
    espaco = _espaco(request)
    hoje = timezone.localdate()
    services.projetar_recorrentes(espaco, hoje)

    inicio, fim = services.limites_do_mes(hoje)
    resumo = services.resumo_periodo(espaco, inicio, fim)
    previsao = services.saldo_previsto(espaco, hoje)

    consumos = [
        services.consumo_do_limite(limite, hoje)
        for limite in Limite.objects.filter(espaco=espaco, ativo=True).select_related("categoria")
    ]

    proximas = (
        Transacao.objects.filter(espaco=espaco, pago=False, data__gte=hoje)
        .select_related("categoria", "conta")
        .order_by("data")[:6]
    )

    return render(
        request,
        "carteira/painel.html",
        {
            "resumo": resumo,
            "previsao": previsao,
            "rosca": graficos.rosca(resumo.por_categoria),
            "consumos": sorted(consumos, key=lambda c: -c["percentual"])[:5],
            "proximas": proximas,
            "recentes": (
                Transacao.objects.filter(espaco=espaco, prevista=False).select_related(
                    "categoria", "conta"
                )[:8]
            ),
            "contas": [
                (conta, services.saldo_da_conta(conta))
                for conta in Conta.objects.filter(espaco=espaco, ativa=True)
            ],
        },
    )


@login_required
def transacoes(request):
    espaco = _espaco(request)
    inicio, fim = _periodo(request)

    consulta = (
        Transacao.objects.filter(espaco=espaco, data__gte=inicio, data__lte=fim)
        .select_related("categoria", "conta", "autor")
        .order_by("-data", "-criada_em")
    )

    categoria = request.GET.get("categoria") or ""
    if categoria:
        consulta = consulta.filter(categoria__nome=categoria)
    tipo = request.GET.get("tipo") or ""
    if tipo in TipoTransacao.values:
        consulta = consulta.filter(tipo=tipo)

    contexto = {
        "transacoes": consulta[:200],
        "inicio": inicio,
        "fim": fim,
        "categoria": categoria,
        "tipo": tipo,
        "categorias": espaco.categorias.filter(ativa=True).order_by("nome"),
        "total": consulta.count(),
    }

    # Requisição do HTMX: devolve só a tabela, não a página.
    if request.headers.get("HX-Request"):
        return render(request, "carteira/_tabela_transacoes.html", contexto)
    return render(request, "carteira/transacoes.html", contexto)


@login_required
def limites(request):
    espaco = _espaco(request)
    hoje = timezone.localdate()
    consumos = [
        services.consumo_do_limite(limite, hoje)
        for limite in Limite.objects.filter(espaco=espaco, ativo=True).select_related("categoria")
    ]
    return render(
        request,
        "carteira/limites.html",
        {"consumos": sorted(consumos, key=lambda c: -c["percentual"])},
    )


@login_required
def relatorios(request):
    espaco = _espaco(request)
    inicio, fim = _periodo(request)
    resumo = services.resumo_periodo(espaco, inicio, fim)

    return render(
        request,
        "carteira/relatorios.html",
        {
            "resumo": resumo,
            "inicio": inicio,
            "fim": fim,
            "periodo": request.GET.get("periodo", "mes"),
            "rosca": graficos.rosca(resumo.por_categoria),
            "barras": graficos.barras(resumo.por_categoria),
            "insights": _insights(resumo),
        },
    )


def _insights(resumo) -> list[str]:
    """Observações sobre o período.

    Determinístico e sem IA: são contas simples sobre números que já estão na
    tela, e pagar por token para dizer "alimentação concentra 73%" seria
    desperdício. A IA entra quando a pessoa pergunta algo que exige julgamento.
    """
    observacoes: list[str] = []
    if not resumo.despesas:
        return ["Nenhuma despesa registrada no período."]

    maior = resumo.maior_categoria
    if maior and maior[2] >= 35:
        observacoes.append(f"{maior[0]} concentra {maior[2]}% das despesas.")

    if resumo.despesas:
        variaveis = round(float(resumo.variaveis / resumo.despesas) * 100)
        if variaveis >= 80:
            observacoes.append(
                f"{variaveis}% dos gastos foram variáveis — é onde há mais espaço para ajustar."
            )
        elif variaveis <= 20:
            observacoes.append(
                f"{100 - variaveis}% dos gastos são fixos, então sobra pouca margem de manobra."
            )

    if resumo.saldo < 0:
        observacoes.append(
            f"As despesas superaram as receitas em R$ {abs(resumo.saldo):.2f} no período."
        )

    return observacoes or ["Nada fora do padrão no período."]


@login_required
def exportar(request):
    """CSV de todas as transações do período. A política de privacidade promete
    portabilidade; esta é a porta."""
    espaco = _espaco(request)
    inicio, fim = _periodo(request)

    resposta = HttpResponse(content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = (
        f'attachment; filename="centavo-{inicio:%Y%m%d}-{fim:%Y%m%d}.csv"'
    )
    # BOM para o Excel em pt-BR abrir com acento correto sem perguntar nada.
    resposta.write("﻿")

    escritor = csv.writer(resposta, delimiter=";")
    escritor.writerow(
        ["codigo", "data", "tipo", "valor", "descricao", "categoria", "conta", "pago", "origem"]
    )
    for t in (
        Transacao.objects.filter(espaco=espaco, data__gte=inicio, data__lte=fim)
        .select_related("categoria", "conta")
        .order_by("data")
    ):
        escritor.writerow(
            [
                t.codigo,
                t.data.isoformat(),
                t.get_tipo_display(),
                f"{t.valor:.2f}".replace(".", ","),
                t.descricao,
                t.categoria.nome if t.categoria else "",
                t.conta.nome if t.conta else "",
                "sim" if t.pago else "não",
                t.get_origem_display(),
            ]
        )
    return resposta
