"""Telas do portal. Implementação completa no passo do portal."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def painel(request):
    return render(request, "carteira/painel.html")


@login_required
def transacoes(request):
    return render(request, "carteira/transacoes.html")


@login_required
def limites(request):
    return render(request, "carteira/limites.html")


@login_required
def relatorios(request):
    return render(request, "carteira/relatorios.html")
