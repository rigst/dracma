"""O portal inteiro em uma página.

Um painel só, com tudo que a pessoa precisa ver, e as edições em diálogo ou na
própria linha. Era possível quebrar em telas de transações, limites e
relatórios, mas isso obrigaria a navegar para responder perguntas que são a
mesma pergunta: "como está o meu mês?".

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
from django.views.decorators.http import require_GET, require_POST

from accounts import espacos
from bot.console import historico

from . import graficos, rateios, services
from .forms import (
    AcertoForm,
    ContaForm,
    DivisaoPadraoForm,
    EntrarNoEspacoForm,
    LimiteForm,
    NomeDoEspacoForm,
    RecorrenteForm,
    TransacaoForm,
)
from .models import Acerto, Conta, Limite, Origem, Recorrente, TipoTransacao, Transacao


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

        usuario.espaco = Espaco.objects.create()
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


def _contexto_do_mes(espaco, usuario, hoje=None) -> dict:
    """Os números do mês, recortados pelo que ESTA pessoa pode ver."""
    hoje = hoje or timezone.localdate()
    services.projetar_recorrentes(espaco, hoje)

    inicio, fim = services.limites_do_mes(hoje)
    resumo = services.resumo_periodo(espaco, inicio, fim, usuario=usuario)
    previsao = services.saldo_previsto(espaco, hoje, usuario=usuario)

    return {
        "hoje": hoje,
        "resumo": resumo,
        "previsao": previsao,
        "rosca": graficos.rosca(resumo.por_categoria),
        "insights": _insights(resumo),
    }


def _consumos(espaco, usuario, hoje=None):
    """Consumo dos limites como ESTA pessoa o enxerga.

    Duas pessoas podem ver percentuais diferentes do mesmo limite, porque veem
    conjuntos diferentes de gastos. É o certo, ver o número do outro seria ver
    o gasto do outro.
    """
    hoje = hoje or timezone.localdate()
    consumos = [
        services.consumo_do_limite(limite, hoje, usuario=usuario)
        for limite in Limite.objects.filter(espaco=espaco, ativo=True).select_related("categoria")
    ]
    return sorted(consumos, key=lambda c: -c["percentual"])


# Os dois fragmentos desenhados por mais de uma view. O nome do arquivo repetido
# à mão é o tipo de coisa que diverge no dia em que um deles for renomeado.
TEMPLATE_COMPARTILHAR = "carteira/_compartilhar.html"
TEMPLATE_FORM_TRANSACAO = "carteira/_form_transacao.html"
TEMPLATE_FORM_SIMPLES = "carteira/_form_simples.html"


@login_required
@require_GET
def painel(request):
    espaco = _espaco(request)
    hoje = timezone.localdate()
    inicio, fim = services.limites_do_mes(hoje)

    contexto = _contexto_do_mes(espaco, request.user, hoje)
    contexto.update(
        {
            "consumos": _consumos(espaco, request.user, hoje),
            "transacoes": _consulta_transacoes(request, espaco)[:60],
            "total": _consulta_transacoes(request, espaco).count(),
            "inicio": inicio,
            "fim": fim,
            "categorias": espaco.categorias.filter(ativa=True).order_by("nome"),
            "contas": [
                (conta, services.saldo_da_conta(conta, usuario=request.user))
                for conta in Conta.objects.filter(espaco=espaco, ativa=True)
            ],
            "recorrentes": services.visiveis_para(
                Recorrente.objects.filter(espaco=espaco, ativo=True), request.user
            ).select_related("categoria"),
            "proximas": services.visiveis_para(
                Transacao.objects.filter(espaco=espaco, pago=False, data__gte=hoje),
                request.user,
            )
            .select_related("categoria", "conta")
            .order_by("data")[:5],
            "membros": espaco.membros.order_by("username"),
            "compartilhado": espaco.membros.count() > 1,
            "acerto": services.acerto_do_periodo(espaco, inicio, fim),
            # A conversa mora no próprio painel: perguntar "quanto sobra?" e
            # ver o número na mesma tela é o ponto.
            "falas": historico(request.user, limite=40),
            # Abre o diálogo de conexão quando a pessoa chega por um link de
            # e-mail ou pelo convite do Telegram.
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
            f"{variaveis}% dos gastos foram variáveis, é onde há mais espaço para ajustar."
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
        services.com_minha_parte(
            services.visiveis_para(
                Transacao.objects.filter(espaco=espaco, data__gte=inicio, data__lte=fim),
                request.user,
            ),
            request.user,
        )
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
@require_GET
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
    saldo, na rosca, nos limites e nas próximas contas ao mesmo tempo, trocar
    um pedaço só deixaria o resto da tela mentindo.
    """
    contexto = _contexto_do_mes(espaco, request.user)
    contexto.update(
        {
            "consumos": _consumos(espaco, request.user),
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
    # alvo é redirecionado aqui, senão o painel inteiro seria injetado na
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
        form = TransacaoForm(request.POST, espaco=espaco, usuario=request.user)
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
                compartilhada=dados["compartilhada"],
                pago_por=dados.get("pago_por") or request.user,
                modo_rateio=dados.get("modo_rateio") or "padrao",
                partes=form.partes_do_rateio(),
            )
            messages.success(request, "Lançamento registrado.")
            return _fragmento_apos_escrita(request, espaco)
    else:
        form = TransacaoForm(espaco=espaco, usuario=request.user)

    return render(
        request,
        TEMPLATE_FORM_TRANSACAO,
        {
            "form": form,
            "titulo": "Novo lançamento",
            "acao": "carteira:nova_transacao",
            "compartilhado": espaco.membros.count() > 1,
        },
    )


@login_required
def editar_transacao(request, codigo):
    espaco = _espaco(request)
    alvo = get_object_or_404(
        services.visiveis_para(Transacao.objects.filter(espaco=espaco), request.user),
        codigo=codigo.upper(),
    )

    if request.method == "POST":
        form = TransacaoForm(request.POST, espaco=espaco, usuario=request.user)
        if form.is_valid():
            dados = form.cleaned_data
            alvo.tipo = dados["tipo"]
            alvo.valor = services.para_decimal(dados["valor"])
            alvo.descricao = dados["descricao"]
            alvo.data = dados["data"]
            alvo.categoria = dados["categoria"]
            alvo.conta = dados["conta"]
            alvo.pago = dados["pago"]
            alvo.compartilhada = dados["compartilhada"]
            alvo.pago_por = dados.get("pago_por") or alvo.pago_por or request.user
            alvo.save()
            rateios.aplicar(alvo, dados.get("modo_rateio") or "padrao", form.partes_do_rateio())
            messages.success(request, f"Lançamento {alvo.codigo} atualizado.")
            return _fragmento_apos_escrita(request, espaco)
    else:
        form = TransacaoForm(
            espaco=espaco,
            usuario=request.user,
            initial={
                "tipo": alvo.tipo,
                "valor": alvo.valor,
                "descricao": alvo.descricao,
                "data": alvo.data,
                "categoria": alvo.categoria_id,
                "conta": alvo.conta_id,
                "pago": alvo.pago,
                "compartilhada": "1" if alvo.compartilhada else "0",
                "pago_por": alvo.pago_por_id,
                # Reabre em "por valor" com as partes atuais: é o modo que
                # mostra a divisão que de fato está gravada, em vez de sugerir
                # recalcular pelo padrão e apagar um ajuste feito à mão.
                "modo_rateio": "valor" if alvo.rateios.exists() else "padrao",
                **{f"val_{r.pessoa_id}": r.valor for r in alvo.rateios.all()},
            },
        )

    return render(
        request,
        TEMPLATE_FORM_TRANSACAO,
        {
            "form": form,
            "titulo": f"Lançamento {alvo.codigo}",
            "acao": "carteira:editar_transacao",
            "codigo": alvo.codigo,
            "compartilhado": espaco.membros.count() > 1,
        },
    )


@login_required
@require_POST
def excluir_transacao(request, codigo):
    espaco = _espaco(request)
    # 404 e não erro de domínio: pedir para apagar o lançamento pessoal de
    # outra pessoa tem que responder como se ele não existisse, que, do ponto
    # de vista de quem pediu, é a verdade.
    alvo = get_object_or_404(
        services.visiveis_para(Transacao.objects.filter(espaco=espaco), request.user),
        codigo=codigo.upper(),
    )
    descricao = alvo.descricao
    alvo.delete()
    messages.info(request, f"“{descricao}” foi apagado.")
    return _fragmento_apos_escrita(request, espaco)


@login_required
@require_POST
def alternar_pago(request, codigo):
    """Dá baixa numa conta direto na linha, sem abrir diálogo."""
    espaco = _espaco(request)
    alvo = get_object_or_404(
        services.visiveis_para(Transacao.objects.filter(espaco=espaco), request.user),
        codigo=codigo.upper(),
    )
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
        TEMPLATE_FORM_SIMPLES,
        {
            "form": form,
            "titulo": "Novo limite",
            "acao": "carteira:novo_limite",
            "ajuda": "A Dracma avisa no Telegram quando o gasto se aproxima do limite.",
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
                autor=request.user,
                compartilhada=dados["compartilhada"],
            )
            messages.success(request, "Recorrente cadastrado.")
            return _fragmento_apos_escrita(request, espaco)
    else:
        form = RecorrenteForm(espaco=espaco)
    return render(
        request,
        TEMPLATE_FORM_SIMPLES,
        {
            "form": form,
            "titulo": "Novo recorrente",
            "acao": "carteira:novo_recorrente",
            "ajuda": "Ganhos e contas fixas entram sozinhos na projeção de todo mês.",
            "compartilhado": espaco.membros.count() > 1,
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
        TEMPLATE_FORM_SIMPLES,
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
@require_GET
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
        [
            "codigo",
            "data",
            "tipo",
            "valor",
            "descricao",
            "parcela",
            "total_parcelas",
            "categoria",
            "conta",
            "pago",
            "quem_ve",
            "origem",
        ]
    )
    for t in (
        services.visiveis_para(
            Transacao.objects.filter(espaco=espaco, data__gte=inicio, data__lte=fim),
            request.user,
        )
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
                t.parcela or "",
                t.total_parcelas or "",
                t.categoria.nome if t.categoria else "",
                t.conta.nome if t.conta else "",
                "sim" if t.pago else "não",
                "espaço" if t.compartilhada else "só eu",
                t.get_origem_display(),
            ]
        )
    return resposta


# ---------------------------------------------------------------------------
# Compartilhar o espaço
# ---------------------------------------------------------------------------


def _contexto_compartilhar(request, espaco, **trocas):
    """O contexto da tela de compartilhar.

    Num lugar só porque três views a desenham, cada uma com um formulário
    diferente ligado: montar o dicionário em cada uma deixaria as telas
    divergindo conforme uma delas ganhasse um campo novo.
    """
    contexto = {
        "espaco": espaco,
        "convite": espacos.convite_vigente(espaco, request.user),
        "membros": espaco.membros.order_by("username"),
        "form": EntrarNoEspacoForm(),
        "form_divisao": DivisaoPadraoForm(espaco=espaco),
        "form_nome": NomeDoEspacoForm(initial={"nome": espaco.nome}),
        "compartilhado": espaco.membros.count() > 1,
    }
    contexto.update(trocas)
    return contexto


@login_required
def compartilhar(request):
    """Convidar alguém, ou entrar no espaço de quem convidou."""
    espaco = _espaco(request)
    form = EntrarNoEspacoForm()

    if request.method == "POST":
        form = EntrarNoEspacoForm(request.POST)
        if form.is_valid():
            try:
                destino = espacos.entrar_com_codigo(request.user, form.cleaned_data["codigo"])
            except espacos.ErroDeEspaco as exc:
                form.add_error("codigo", str(exc))
            else:
                messages.success(request, f"Você entrou em “{destino.nome}”.")
                return _fragmento_apos_escrita(request, destino)

    return render(
        request,
        TEMPLATE_COMPARTILHAR,
        _contexto_compartilhar(request, espaco, form=form),
    )


@login_required
@require_POST
def registrar_acerto(request):
    """Marca a dívida do mês como paga.

    O valor vem do formulário, e não do saldo calculado na hora: quem acerta
    pode pagar parte, e recalcular aqui ignoraria isso. O saldo do mês passa a
    descontar o que já foi quitado.
    """
    espaco = _espaco(request)
    form = AcertoForm(request.POST, espaco=espaco)
    if form.is_valid():
        dados = form.cleaned_data
        Acerto.objects.create(
            espaco=espaco,
            quem_pagou=dados["pagou"],
            quem_recebeu=dados["recebeu"],
            valor=dados["valor"],
            referencia=dados["referencia"],
            registrado_por=request.user,
        )
        messages.success(request, "Acerto registrado.")
    else:
        messages.error(request, form.errors.as_text()[:200] or "Não consegui registrar o acerto.")
    return _fragmento_apos_escrita(request, espaco)


@login_required
@require_POST
def desfazer_acerto(request, pk):
    espaco = _espaco(request)
    get_object_or_404(Acerto, espaco=espaco, pk=pk).delete()
    messages.info(request, "Acerto desfeito.")
    return _fragmento_apos_escrita(request, espaco)


@login_required
@require_POST
def divisao_padrao(request):
    """Como o espaço divide, quando ninguém disser o contrário."""
    espaco = _espaco(request)
    form = DivisaoPadraoForm(request.POST, espaco=espaco)
    if form.is_valid():
        rateios.definir_padrao(
            espaco,
            form.cleaned_data.get("percentuais")
            if form.cleaned_data["modo"] == "percentual"
            else None,
        )
        messages.success(request, "Divisão padrão salva.")
        return _fragmento_apos_escrita(request, espaco)

    return render(
        request,
        TEMPLATE_COMPARTILHAR,
        _contexto_compartilhar(request, espaco, form_divisao=form),
    )


@login_required
@require_POST
def renomear_espaco(request):
    """Troca o nome do espaço.

    Não fecha o diálogo no sucesso, diferente de quem grava lançamento: o nome
    não aparece no painel, então fechar não mostraria nada mudando. A pessoa
    vê o campo com o nome novo e o título acima dele.
    """
    espaco = _espaco(request)
    form = NomeDoEspacoForm(request.POST)
    if form.is_valid():
        espaco.nome = form.cleaned_data["nome"]
        espaco.save(update_fields=["nome"])
        form = NomeDoEspacoForm(initial={"nome": espaco.nome})

    return render(
        request,
        TEMPLATE_COMPARTILHAR,
        _contexto_compartilhar(request, espaco, form_nome=form),
    )


@login_required
@require_POST
def sair_do_espaco(request):
    try:
        espacos.sair_do_espaco(request.user)
    except espacos.ErroDeEspaco as exc:
        messages.error(request, str(exc))
    else:
        messages.info(
            request,
            "Você saiu do espaço. O que era compartilhado ficou lá; o que era seu veio junto.",
        )
    return _fragmento_apos_escrita(request, _espaco(request))
