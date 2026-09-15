"""O portal inteiro em uma página.

Um painel só, com tudo que a pessoa precisa ver, e as edições em diálogo ou na
própria linha. Era possível quebrar em telas de transações, limites e
relatórios, mas isso obrigaria a navegar para responder perguntas que são a
mesma pergunta — "como está o meu mês?".

As views de fragmento existem para o HTMX trocar só o pedaço que mudou: depois
de lançar uma despesa, volta a tabela e os totais, não a página.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from zap.console import historico

from . import graficos, services
from .forms import ContaForm, LimiteForm, RecorrenteForm, TransacaoForm
from .models import Conta, Limite, Origem, Recorrente, TipoTransacao, Transacao


def _espaco(request):
    """O espaço do usuário, criado na hora se ainda não existir.

    Toda conta precisa de um: contas antigas, criadas antes do app, e as que
    nascem por caminhos que não passam pelo cadastro (superusuário pelo
    `createsuperuser`, por exemplo) chegariam aqui sem espaço e derrubariam o
    painel inteiro com AttributeError.
    """
    usuario = request.user
    if usuario.espaco_id is None:
        from accounts.models import Espaco

        from .seeds import semear_categorias

        usuario.espaco = Espaco.objects.create(nome="Meu espaço")
        usuario.save(update_fields=["espaco"])
        semear_categorias(usuario.espaco)
    return usuario.espaco


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


# ---------------------------------------------------------------------------
# Painel
# ---------------------------------------------------------------------------


def _contexto_do_mes(espaco, hoje=None) -> dict:
    """Os números do mês. Usado pelo painel e pelo fragmento de totais."""
    hoje = hoje or timezone.localdate()
    services.projetar_recorrentes(espaco, hoje)

    inicio, fim = services.limites_do_mes(hoje)
    resumo = services.resumo_periodo(espaco, inicio, fim)
    previsao = services.saldo_previsto(espaco, hoje)

    return {
        "hoje": hoje,
        "resumo": resumo,
        "previsao": previsao,
        "rosca": graficos.rosca(resumo.por_categoria),
        "insights": _insights(resumo),
    }


def _consumos(espaco, hoje=None):
    hoje = hoje or timezone.localdate()
    consumos = [
        services.consumo_do_limite(limite, hoje)
        for limite in Limite.objects.filter(espaco=espaco, ativo=True).select_related("categoria")
    ]
    return sorted(consumos, key=lambda c: -c["percentual"])


@login_required
def painel(request):
    espaco = _espaco(request)
    hoje = timezone.localdate()
    inicio, fim = services.limites_do_mes(hoje)

    contexto = _contexto_do_mes(espaco, hoje)
    contexto.update(
        {
            "consumos": _consumos(espaco, hoje),
            "transacoes": _consulta_transacoes(request, espaco)[:60],
            "total": _consulta_transacoes(request, espaco).count(),
            "inicio": inicio,
            "fim": fim,
            "categorias": espaco.categorias.filter(ativa=True).order_by("nome"),
            "contas": [
                (conta, services.saldo_da_conta(conta))
                for conta in Conta.objects.filter(espaco=espaco, ativa=True)
            ],
            "recorrentes": Recorrente.objects.filter(espaco=espaco, ativo=True).select_related(
                "categoria"
            ),
            "proximas": (
                Transacao.objects.filter(espaco=espaco, pago=False, data__gte=hoje)
                .select_related("categoria", "conta")
                .order_by("data")[:5]
            ),
            # A conversa mora no próprio painel: perguntar "quanto sobra?" e
            # ver o número na mesma tela é o ponto.
            "falas": historico(request.user, limite=40),
            # Abre o diálogo de conexão quando a pessoa chega por um link de
            # e-mail ou pelo convite do WhatsApp.
            "abrir_conectar": request.GET.get("conectar") == "1",
        }
    )
    return render(request, "carteira/painel.html", contexto)


def _insights(resumo) -> list[str]:
    """Observações sobre o mês.

    Determinístico e sem IA: são contas simples sobre números que já estão na
    tela, e pagar por token para dizer "alimentação concentra 73%" seria
    desperdício. A IA entra quando a pergunta exige julgamento.
    """
    observacoes: list[str] = []
    if not resumo.despesas:
        return ["Nenhuma despesa registrada no período."]

    maior = resumo.maior_categoria
    if maior and maior[2] >= 35:
        observacoes.append(f"{maior[0]} concentra {maior[2]}% das despesas.")

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


# ---------------------------------------------------------------------------
# Fragmentos
# ---------------------------------------------------------------------------


def _consulta_transacoes(request, espaco):
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
    return consulta


@login_required
def transacoes(request):
    """Fragmento da tabela. Responde aos filtros sem recarregar a página."""
    espaco = _espaco(request)
    consulta = _consulta_transacoes(request, espaco)
    inicio, fim = _periodo(request)
    return render(
        request,
        "carteira/_transacoes.html",
        {
            "transacoes": consulta[:60],
            "total": consulta.count(),
            "inicio": inicio,
            "fim": fim,
        },
    )


def _fragmento_apos_escrita(request, espaco):
    """Depois de gravar, devolve o painel inteiro atualizado.

    A alternativa seria devolver só a tabela, mas um lançamento novo mexe no
    saldo, na rosca, nos limites e nas próximas contas ao mesmo tempo — trocar
    um pedaço só deixaria o resto da tela mentindo.
    """
    contexto = _contexto_do_mes(espaco)
    contexto.update(
        {
            "consumos": _consumos(espaco),
            "transacoes": _consulta_transacoes(request, espaco)[:60],
            "total": _consulta_transacoes(request, espaco).count(),
            "contas": [
                (conta, services.saldo_da_conta(conta))
                for conta in Conta.objects.filter(espaco=espaco, ativa=True)
            ],
            "recorrentes": Recorrente.objects.filter(espaco=espaco, ativo=True).select_related(
                "categoria"
            ),
            "proximas": (
                Transacao.objects.filter(espaco=espaco, pago=False, data__gte=timezone.localdate())
                .select_related("categoria", "conta")
                .order_by("data")[:5]
            ),
        }
    )
    resposta = render(request, "carteira/_painel_corpo.html", contexto)
    # Os formulários miram o próprio diálogo, para que um erro de validação
    # volte PARA DENTRO dele. No sucesso não há formulário para mostrar, e o
    # alvo é redirecionado aqui — senão o painel inteiro seria injetado na
    # caixinha do diálogo.
    resposta["HX-Retarget"] = "#painel-corpo"
    resposta["HX-Reswap"] = "innerHTML"
    # Fecha o diálogo aberto: quem escuta é o app.js.
    resposta["HX-Trigger"] = "gravado"
    return resposta


# ---------------------------------------------------------------------------
# Lançamentos
# ---------------------------------------------------------------------------


@login_required
def nova_transacao(request):
    espaco = _espaco(request)
    if request.method == "POST":
        form = TransacaoForm(request.POST, espaco=espaco)
        if form.is_valid():
            dados = form.cleaned_data
            services.registrar_transacao(
                espaco=espaco,
                valor=dados["valor"],
                descricao=dados["descricao"],
                tipo=dados["tipo"],
                categoria=dados["categoria"].nome if dados["categoria"] else None,
                conta=dados["conta"].nome if dados["conta"] else None,
                data_lancamento=dados["data"],
                pago=dados["pago"],
                origem=Origem.PORTAL,
                autor=request.user,
            )
            messages.success(request, "Lançamento registrado.")
            return _fragmento_apos_escrita(request, espaco)
    else:
        form = TransacaoForm(espaco=espaco)

    return render(
        request,
        "carteira/_form_transacao.html",
        {"form": form, "titulo": "Novo lançamento", "acao": "carteira:nova_transacao"},
    )


@login_required
def editar_transacao(request, codigo):
    espaco = _espaco(request)
    alvo = get_object_or_404(Transacao, espaco=espaco, codigo=codigo.upper())

    if request.method == "POST":
        form = TransacaoForm(request.POST, espaco=espaco)
        if form.is_valid():
            dados = form.cleaned_data
            alvo.tipo = dados["tipo"]
            alvo.valor = services.para_decimal(dados["valor"])
            alvo.descricao = dados["descricao"]
            alvo.data = dados["data"]
            alvo.categoria = dados["categoria"]
            alvo.conta = dados["conta"]
            alvo.pago = dados["pago"]
            alvo.save()
            messages.success(request, f"Lançamento {alvo.codigo} atualizado.")
            return _fragmento_apos_escrita(request, espaco)
    else:
        form = TransacaoForm(
            espaco=espaco,
            initial={
                "tipo": alvo.tipo,
                "valor": alvo.valor,
                "descricao": alvo.descricao,
                "data": alvo.data,
                "categoria": alvo.categoria_id,
                "conta": alvo.conta_id,
                "pago": alvo.pago,
            },
        )

    return render(
        request,
        "carteira/_form_transacao.html",
        {
            "form": form,
            "titulo": f"Lançamento {alvo.codigo}",
            "acao": "carteira:editar_transacao",
            "codigo": alvo.codigo,
        },
    )


@login_required
@require_POST
def excluir_transacao(request, codigo):
    espaco = _espaco(request)
    resumo = services.excluir_transacao(espaco=espaco, codigo=codigo)
    messages.info(request, f"“{resumo['descricao']}” foi apagado.")
    return _fragmento_apos_escrita(request, espaco)


@login_required
@require_POST
def alternar_pago(request, codigo):
    """Dá baixa numa conta direto na linha, sem abrir diálogo."""
    espaco = _espaco(request)
    alvo = get_object_or_404(Transacao, espaco=espaco, codigo=codigo.upper())
    alvo.pago = not alvo.pago
    alvo.prevista = False if alvo.pago else alvo.prevista
    alvo.save(update_fields=["pago", "prevista", "atualizada_em"])
    return _fragmento_apos_escrita(request, espaco)


# ---------------------------------------------------------------------------
# Limites, recorrentes e contas
# ---------------------------------------------------------------------------


@login_required
def novo_limite(request):
    espaco = _espaco(request)
    if request.method == "POST":
        form = LimiteForm(request.POST, espaco=espaco)
        if form.is_valid():
            dados = form.cleaned_data
            services.criar_limite(
                espaco=espaco,
                valor=dados["valor"],
                categoria=dados["categoria"].nome if dados["categoria"] else None,
                rotulo=dados["rotulo"],
                dias=dados["dias"],
            )
            messages.success(request, "Limite criado.")
            return _fragmento_apos_escrita(request, espaco)
    else:
        form = LimiteForm(espaco=espaco)
    return render(
        request,
        "carteira/_form_simples.html",
        {
            "form": form,
            "titulo": "Novo limite",
            "acao": "carteira:novo_limite",
            "ajuda": "A Dracma avisa no WhatsApp quando o gasto se aproxima do limite.",
        },
    )


@login_required
@require_POST
def excluir_limite(request, pk):
    espaco = _espaco(request)
    get_object_or_404(Limite, espaco=espaco, pk=pk).delete()
    messages.info(request, "Limite removido.")
    return _fragmento_apos_escrita(request, espaco)


@login_required
def novo_recorrente(request):
    espaco = _espaco(request)
    if request.method == "POST":
        form = RecorrenteForm(request.POST, espaco=espaco)
        if form.is_valid():
            dados = form.cleaned_data
            services.criar_recorrente(
                espaco=espaco,
                descricao=dados["descricao"],
                valor=dados["valor"],
                dia_do_mes=dados["dia_do_mes"],
                tipo=dados["tipo"],
                categoria=dados["categoria"].nome if dados["categoria"] else None,
                conta=dados["conta"].nome if dados["conta"] else None,
            )
            messages.success(request, "Recorrente cadastrado.")
            return _fragmento_apos_escrita(request, espaco)
    else:
        form = RecorrenteForm(espaco=espaco)
    return render(
        request,
        "carteira/_form_simples.html",
        {
            "form": form,
            "titulo": "Novo recorrente",
            "acao": "carteira:novo_recorrente",
            "ajuda": "Ganhos e contas fixas entram sozinhos na projeção de todo mês.",
        },
    )


@login_required
@require_POST
def excluir_recorrente(request, pk):
    espaco = _espaco(request)
    get_object_or_404(Recorrente, espaco=espaco, pk=pk).delete()
    messages.info(request, "Recorrente removido.")
    return _fragmento_apos_escrita(request, espaco)


@login_required
def nova_conta(request):
    espaco = _espaco(request)
    if request.method == "POST":
        form = ContaForm(request.POST, espaco=espaco)
        if form.is_valid():
            dados = dict(form.cleaned_data)
            dados["saldo_inicial"] = dados.get("saldo_inicial") or 0
            Conta.objects.create(espaco=espaco, **dados)
            messages.success(request, "Conta criada.")
            return _fragmento_apos_escrita(request, espaco)
    else:
        form = ContaForm(espaco=espaco)
    return render(
        request,
        "carteira/_form_simples.html",
        {
            "form": form,
            "titulo": "Nova conta",
            "acao": "carteira:nova_conta",
            "ajuda": "Onde o dinheiro está: banco, cartão ou a carteira mesmo.",
        },
    )


# ---------------------------------------------------------------------------
# Exportação
# ---------------------------------------------------------------------------


@login_required
def exportar(request):
    """CSV do período. A política de privacidade promete portabilidade; esta é
    a porta."""
    espaco = _espaco(request)
    inicio, fim = _periodo(request)

    resposta = HttpResponse(content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = (
        f'attachment; filename="dracma-{inicio:%Y%m%d}-{fim:%Y%m%d}.csv"'
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
