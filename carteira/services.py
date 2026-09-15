"""Serviços do domínio financeiro.

Ponto único de escrita. O agente de IA e as telas do portal chamam as MESMAS
funções daqui: a resposta da Claude nunca escreve direto no banco — a tool
devolve argumentos e é este módulo que valida e persiste. Sem isso, teríamos
duas regras de negócio divergindo em silêncio.
"""

from __future__ import annotations

import calendar
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
from django.db.models import (
    Case,
    DecimalField,
    Exists,
    F,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce

_DINHEIRO = DecimalField(max_digits=12, decimal_places=2)

from .models import (
    Categoria,
    Conta,
    Limite,
    Origem,
    Recorrente,
    TipoTransacao,
    Transacao,
)


class ErroDeDominio(Exception):
    """Entrada inválida. A mensagem é mostrada ao usuário como está, então
    precisa ser escrita para ele — não para o log."""


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento. Usado para casar 'alimentacao' com 'Alimentação'
    — o que a IA escreve nem sempre bate com o que está gravado."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return sem_acento.strip().lower()


def para_decimal(valor) -> Decimal:
    """Aceita 34, '34', '34,50' e '1.234,56'.

    Vale a pena ser tolerante: o valor chega de transcrição de áudio e de
    leitura de comprovante, onde o formato varia. O que NÃO se aceita é float
    — a conversão passa por str para não herdar o erro de representação.
    """
    if isinstance(valor, Decimal):
        quantia = valor
    elif isinstance(valor, int):
        quantia = Decimal(valor)
    else:
        texto = str(valor).strip().replace("R$", "").replace(" ", "")
        if "," in texto:
            # Formato pt-BR: o ponto é separador de milhar.
            texto = texto.replace(".", "").replace(",", ".")
        try:
            quantia = Decimal(texto)
        except (InvalidOperation, ValueError) as exc:
            raise ErroDeDominio(f"Não entendi o valor “{valor}”.") from exc

    quantia = quantia.quantize(Decimal("0.01"))
    if quantia <= 0:
        raise ErroDeDominio("O valor precisa ser maior que zero.")
    return quantia


# ---------------------------------------------------------------------------
# Busca de categoria e conta
# ---------------------------------------------------------------------------


def achar_categoria(espaco, nome: str | None, tipo: str = TipoTransacao.DESPESA):
    """Casa por nome normalizado; cria se não existir.

    Criar em vez de recusar é deliberado: o usuário disse "gastei 40 em
    pet shop" e o objetivo é registrar, não abrir uma negociação sobre
    taxonomia. As categorias personalizadas são ilimitadas de qualquer forma.
    """
    if not nome:
        return None
    alvo = normalizar(nome)
    for categoria in Categoria.objects.filter(espaco=espaco, ativa=True):
        if normalizar(categoria.nome) == alvo:
            return categoria
    return Categoria.objects.create(espaco=espaco, nome=nome.strip()[:60], tipo=tipo)


def achar_conta(espaco, nome: str | None):
    """Casa por nome normalizado, com correspondência parcial.

    Diferente da categoria, NÃO cria: conta é coisa que a pessoa configura uma
    vez. Inventar "Nubannk" por causa de um erro de transcrição espalharia
    saldo por contas fantasma.
    """
    if not nome:
        return None
    alvo = normalizar(nome)
    contas = list(Conta.objects.filter(espaco=espaco, ativa=True))
    for conta in contas:
        if normalizar(conta.nome) == alvo:
            return conta
    for conta in contas:
        if alvo in normalizar(conta.nome) or normalizar(conta.nome) in alvo:
            return conta
    return None


# ---------------------------------------------------------------------------
# Transações
# ---------------------------------------------------------------------------


@db_transaction.atomic
def registrar_transacao(
    *,
    espaco,
    valor,
    descricao: str,
    tipo: str = TipoTransacao.DESPESA,
    categoria: str | None = None,
    conta: str | None = None,
    data_lancamento: date | None = None,
    pago: bool = True,
    origem: str = Origem.PORTAL,
    autor=None,
    # Pessoal por padrão: compartilhar é a escolha ativa. Errar para o lado de
    # guardar não custa nada; errar para o lado de expor não tem desfazer.
    compartilhada: bool = False,
    pago_por=None,
    modo_rateio: str = "padrao",
    partes: dict | None = None,
    observacao: str = "",
) -> Transacao:
    if tipo not in TipoTransacao.values:
        raise ErroDeDominio(f"Tipo de transação desconhecido: {tipo}.")

    descricao = (descricao or "").strip()[:140]
    if not descricao:
        raise ErroDeDominio("A transação precisa de uma descrição.")

    dono = dono_presumido(espaco, autor)
    transacao = Transacao.objects.create(
        espaco=espaco,
        autor=dono,
        tipo=tipo,
        valor=para_decimal(valor),
        descricao=descricao,
        data=data_lancamento or date.today(),
        categoria=achar_categoria(espaco, categoria, tipo),
        conta=achar_conta(espaco, conta),
        pago=pago,
        origem=origem,
        compartilhada=compartilhada,
        # Quem pagou, quando ninguém disse, é quem lançou.
        pago_por=pago_por or dono,
        observacao=observacao or "",
    )

    from . import rateios

    rateios.aplicar(transacao, modo_rateio, partes)
    return transacao


def buscar_por_codigo(espaco, codigo: str, usuario=None) -> Transacao:
    """Acha pelo código, dentro do que a pessoa PODE ver.

    Sem o recorte, saber o código de cinco caracteres bastaria para editar ou
    apagar o lançamento pessoal de quem divide o espaço — e o erro apareceria
    como "não achei", que é justamente o que deve acontecer.
    """
    codigo = (codigo or "").strip().upper()
    try:
        return visiveis_para(Transacao.objects.filter(espaco=espaco), usuario).get(codigo=codigo)
    except Transacao.DoesNotExist as exc:
        raise ErroDeDominio(f"Não achei nenhum lançamento com o código {codigo}.") from exc


@db_transaction.atomic
def editar_transacao(*, espaco, codigo: str, usuario=None, **campos) -> Transacao:
    alvo = buscar_por_codigo(espaco, codigo, usuario)
    mudou = []

    if campos.get("valor") is not None:
        alvo.valor = para_decimal(campos["valor"])
        mudou.append("valor")
    if campos.get("descricao"):
        alvo.descricao = campos["descricao"].strip()[:140]
        mudou.append("descricao")
    if campos.get("data_lancamento"):
        alvo.data = campos["data_lancamento"]
        mudou.append("data")
    if campos.get("categoria"):
        alvo.categoria = achar_categoria(espaco, campos["categoria"], alvo.tipo)
        mudou.append("categoria")
    if campos.get("conta"):
        conta = achar_conta(espaco, campos["conta"])
        if conta is None:
            raise ErroDeDominio(f"Não achei a conta “{campos['conta']}”.")
        alvo.conta = conta
        mudou.append("conta")
    if campos.get("pago") is not None:
        alvo.pago = bool(campos["pago"])
        mudou.append("pago")

    if not mudou:
        raise ErroDeDominio("Nada para alterar.")

    alvo.save(update_fields=[*mudou, "atualizada_em"])
    return alvo


@db_transaction.atomic
def excluir_transacao(*, espaco, codigo: str, usuario=None) -> dict:
    alvo = buscar_por_codigo(espaco, codigo, usuario)
    resumo = {"codigo": alvo.codigo, "descricao": alvo.descricao, "valor": alvo.valor}
    alvo.delete()
    return resumo


# ---------------------------------------------------------------------------
# Períodos
# ---------------------------------------------------------------------------


def limites_do_mes(referencia: date | None = None) -> tuple[date, date]:
    referencia = referencia or date.today()
    ultimo = calendar.monthrange(referencia.year, referencia.month)[1]
    return referencia.replace(day=1), referencia.replace(day=ultimo)


def dia_valido(ano: int, mes: int, dia: int) -> date:
    """Dia 31 em mês de 30 cai no último dia — não pula o mês.

    Sem isto, um aluguel no dia 31 simplesmente não seria projetado em abril,
    junho, setembro e novembro, e o saldo previsto ficaria otimista sem avisar.
    """
    ultimo = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, min(dia, ultimo))


# ---------------------------------------------------------------------------
# Visibilidade
# ---------------------------------------------------------------------------


def dono_presumido(espaco, autor):
    """Quem assina um lançamento quando ninguém disse.

    Um lançamento pessoal SEM autor não é de ninguém e some para todo mundo —
    é a armadilha do padrão pessoal. Num espaço de uma pessoa só não existe
    ambiguidade: o dono é ela, e atribuir aqui evita o registro órfão que
    apareceria de comandos, importações e de qualquer caminho sem request.

    Com duas pessoas dentro não se adivinha: chutar o dono de um gasto pessoal
    seria mostrar a gasto de alguém a quem não deve vê-lo.
    """
    if autor is not None:
        return autor
    membros = list(espaco.membros.all()[:2])
    return membros[0] if len(membros) == 1 else None


def com_minha_parte(consulta, usuario):
    """Anota `minha_parte`: quanto do lançamento cabe a ESTA pessoa.

    Sem rateio, a parte é o valor cheio — é o caso do lançamento pessoal e do
    compartilhado que ninguém dividiu. COM rateio, é a linha da pessoa, e zero
    se ela não tem linha nenhuma (um gasto da casa que coube todo ao outro).

    Existe porque "quanto isso me custou" e "quanto saiu da conta" deixaram de
    ser o mesmo número: a conta de luz de R$ 310 paga pela Ana e dividida ao
    meio tira R$ 310 da conta dela e custa R$ 155 a cada uma.
    """
    if usuario is None:
        return consulta.annotate(minha_parte=F("valor"))

    from .models import Rateio

    minha = Rateio.objects.filter(transacao=OuterRef("pk"), pessoa=usuario).values("valor")[:1]
    qualquer = Rateio.objects.filter(transacao=OuterRef("pk"))

    return consulta.annotate(
        minha_parte=Case(
            When(
                Exists(qualquer),
                then=Coalesce(Subquery(minha, output_field=_DINHEIRO), Value(Decimal("0"))),
            ),
            default=F("valor"),
            output_field=_DINHEIRO,
        )
    )


def visiveis_para(consulta, usuario):
    """Filtra o que uma pessoa pode ver dentro do próprio espaço.

    A regra é uma só: **ou o lançamento é compartilhado, ou é seu**. Nada de
    "quase" — se ficasse espalhada, o primeiro relatório novo esqueceria dela e
    um gasto pessoal apareceria no total do casal.

    `usuario=None` significa "sem recorte", e é o que as rotinas de manutenção
    usam. Toda consulta de TELA passa o usuário.
    """
    if usuario is None:
        return consulta
    return consulta.filter(Q(compartilhada=True) | Q(autor=usuario))


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------


@dataclass
class Resumo:
    inicio: date
    fim: date
    receitas: Decimal
    despesas: Decimal
    por_categoria: list[tuple[str, Decimal]]
    fixas: Decimal
    variaveis: Decimal

    @property
    def saldo(self) -> Decimal:
        return self.receitas - self.despesas

    @property
    def maior_categoria(self) -> tuple[str, Decimal, int] | None:
        if not self.por_categoria or not self.despesas:
            return None
        nome, total = self.por_categoria[0]
        return nome, total, int(total / self.despesas * 100)


def _base(espaco, inicio: date, fim: date, incluir_previstas: bool, usuario=None):
    consulta = Transacao.objects.filter(espaco=espaco, data__gte=inicio, data__lte=fim)
    if not incluir_previstas:
        consulta = consulta.filter(prevista=False)
    return com_minha_parte(visiveis_para(consulta, usuario), usuario)


def total_gasto(
    espaco,
    inicio: date,
    fim: date,
    categoria=None,
    conta=None,
    incluir_previstas: bool = False,
    usuario=None,
) -> Decimal:
    consulta = _base(espaco, inicio, fim, incluir_previstas, usuario).filter(
        tipo=TipoTransacao.DESPESA
    )
    if categoria is not None:
        consulta = consulta.filter(categoria=categoria)
    if conta is not None:
        consulta = consulta.filter(conta=conta)
    return consulta.aggregate(total=Sum("minha_parte"))["total"] or Decimal("0")


def resumo_periodo(
    espaco, inicio: date, fim: date, incluir_previstas: bool = False, usuario=None
) -> Resumo:
    consulta = _base(espaco, inicio, fim, incluir_previstas, usuario)

    receitas = consulta.filter(tipo=TipoTransacao.RECEITA).aggregate(t=Sum("minha_parte"))[
        "t"
    ] or Decimal("0")
    despesas = consulta.filter(tipo=TipoTransacao.DESPESA).aggregate(t=Sum("minha_parte"))[
        "t"
    ] or Decimal("0")

    agrupado = (
        consulta.filter(tipo=TipoTransacao.DESPESA)
        .values("categoria__nome", "categoria__emoji")
        .annotate(total=Sum("minha_parte"))
        .order_by("-total")
    )
    por_categoria = [
        (
            f"{linha['categoria__emoji'] or ''} {linha['categoria__nome'] or 'Sem categoria'}".strip(),
            linha["total"],
        )
        for linha in agrupado
    ]

    fixas = consulta.filter(tipo=TipoTransacao.DESPESA, categoria__fixa=True).aggregate(
        t=Sum("minha_parte")
    )["t"] or Decimal("0")

    return Resumo(
        inicio=inicio,
        fim=fim,
        receitas=receitas,
        despesas=despesas,
        por_categoria=por_categoria,
        fixas=fixas,
        variaveis=despesas - fixas,
    )


def saldo_previsto(espaco, referencia: date | None = None, usuario=None) -> dict:
    """O que ainda vem no mês.

    É o número central do planejamento: junta o que já aconteceu com o que
    está projetado, para a pessoa ver o aperto no dia 5 em vez de no dia 25.
    """
    inicio, fim = limites_do_mes(referencia)
    realizado = resumo_periodo(espaco, inicio, fim, incluir_previstas=False, usuario=usuario)
    completo = resumo_periodo(espaco, inicio, fim, incluir_previstas=True, usuario=usuario)

    return {
        "inicio": inicio,
        "fim": fim,
        "receitas_realizadas": realizado.receitas,
        "despesas_realizadas": realizado.despesas,
        "receitas_previstas": completo.receitas,
        "despesas_previstas": completo.despesas,
        "saldo_realizado": realizado.saldo,
        "saldo_previsto": completo.saldo,
        "a_pagar": completo.despesas - realizado.despesas,
        "a_receber": completo.receitas - realizado.receitas,
    }


def saldo_da_conta(conta: Conta, usuario=None) -> Decimal:
    """Calculado, nunca desnormalizado: um campo `saldo` gravado dessincroniza
    na primeira edição de transação antiga.

    Usa o valor CHEIO, e não a fatia de cada um: a conta de luz de R$ 310 paga
    da conta conjunta tira R$ 310 dela, independentemente de como as pessoas
    dividiram o custo entre si. Saldo é caixa; rateio é custo.

    Recortado pela visibilidade: numa conta conjunta, quem não enxerga o gasto
    pessoal do outro também não pode ver o efeito dele no saldo — senão a
    diferença entre dois números entregaria o lançamento escondido.
    """
    movimento = visiveis_para(
        Transacao.objects.filter(conta=conta, prevista=False, pago=True), usuario
    ).aggregate(
        receitas=Sum("valor", filter=Q(tipo=TipoTransacao.RECEITA)),
        despesas=Sum("valor", filter=Q(tipo=TipoTransacao.DESPESA)),
    )
    recebido = visiveis_para(
        Transacao.objects.filter(
            conta_destino=conta, prevista=False, pago=True, tipo=TipoTransacao.TRANSFERENCIA
        ),
        usuario,
    ).aggregate(t=Sum("valor"))["t"] or Decimal("0")

    return (
        conta.saldo_inicial
        + (movimento["receitas"] or Decimal("0"))
        - (movimento["despesas"] or Decimal("0"))
        + recebido
    )


# ---------------------------------------------------------------------------
# Limites
# ---------------------------------------------------------------------------


def criar_limite(
    *, espaco, valor, categoria: str | None = None, rotulo: str = "", dias: int | None = None
) -> Limite:
    """`dias` preenchido cria limite temporário ("R$ 200 pra presente"),
    acompanhado à parte do orçamento padrão."""
    alvo = achar_categoria(espaco, categoria) if categoria else None
    hoje = date.today()
    return Limite.objects.create(
        espaco=espaco,
        categoria=alvo,
        rotulo=rotulo.strip()[:60],
        valor=para_decimal(valor),
        inicio=hoje if dias else None,
        fim=hoje + timedelta(days=dias) if dias else None,
    )


def consumo_do_limite(limite: Limite, referencia: date | None = None, usuario=None) -> dict:
    """Quanto já saiu contra este teto, PARA QUEM ESTÁ OLHANDO.

    O limite é do espaço, mas o consumo é recortado pela visibilidade de quem
    pergunta: cada pessoa vê o compartilhado mais o próprio. Duas pessoas podem
    ver percentuais diferentes do mesmo limite — e isso é o certo, porque elas
    veem conjuntos diferentes de gastos.

    A alternativa seria contar só o compartilhado, mas aí quem usa sozinho
    ficaria com o limite parado em zero, já que o padrão dos lançamentos é
    pessoal. O vazamento que essa alternativa evitava é resolvido no alerta:
    cada pessoa recebe o número dela (ver `carteira.tasks.verificar_limites`).

    O limite temporário conta desde a criação até o fim; o mensal conta o mês
    corrente. Misturar os dois faria o presente de aniversário estourar o
    orçamento de mercado.
    """
    if limite.temporario:
        inicio, fim = limite.inicio, limite.fim
    else:
        inicio, fim = limites_do_mes(referencia)

    gasto = total_gasto(limite.espaco, inicio, fim, categoria=limite.categoria, usuario=usuario)
    percentual = int(gasto / limite.valor * 100) if limite.valor else 0

    return {
        "limite": limite,
        "inicio": inicio,
        "fim": fim,
        "gasto": gasto,
        "restante": limite.valor - gasto,
        "percentual": percentual,
        "estourado": gasto > limite.valor,
    }


# ---------------------------------------------------------------------------
# Recorrentes
# ---------------------------------------------------------------------------


def criar_recorrente(
    *,
    espaco,
    descricao: str,
    valor,
    dia_do_mes: int,
    tipo: str = TipoTransacao.DESPESA,
    categoria: str | None = None,
    conta: str | None = None,
    autor=None,
    compartilhada: bool = False,
) -> Recorrente:
    if not 1 <= int(dia_do_mes) <= 31:
        raise ErroDeDominio("O dia do mês precisa estar entre 1 e 31.")

    return Recorrente.objects.create(
        espaco=espaco,
        descricao=descricao.strip()[:140],
        valor=para_decimal(valor),
        dia_do_mes=int(dia_do_mes),
        tipo=tipo,
        categoria=achar_categoria(espaco, categoria, tipo),
        conta=achar_conta(espaco, conta),
        autor=dono_presumido(espaco, autor),
        compartilhada=compartilhada,
    )


@db_transaction.atomic
def projetar_recorrentes(espaco, referencia: date | None = None, meses: int = 2) -> int:
    """Materializa as transações previstas. Idempotente.

    Projeta uma JANELA para a frente (mês de referência + `meses`), não só o
    mês corrente: o planejamento precisa mostrar os próximos 30 dias mesmo
    quando a virada do mês está perto.

    Duas guardas que parecem detalhe e não são:

    - Vencimento anterior ao `inicio` da regra é pulado. Quem cadastra
      "aluguel todo dia 5" no dia 14 não quer um lançamento *previsto* no dia 5
      que já passou — aquilo ou já foi registrado, ou não aconteceu.
    - A checagem de existência é por (regra, data), porque a função roda
      diariamente E a cada consulta de planejamento.
    """
    referencia = referencia or date.today()
    criadas = 0
    regras = list(Recorrente.objects.filter(espaco=espaco, ativo=True))
    if not regras:
        return 0

    for passo in range(meses):
        ano = referencia.year + (referencia.month - 1 + passo) // 12
        mes = (referencia.month - 1 + passo) % 12 + 1

        for regra in regras:
            vencimento = dia_valido(ano, mes, regra.dia_do_mes)
            if vencimento < regra.inicio or (regra.fim and vencimento > regra.fim):
                continue

            marcador = f"[rec:{regra.pk}]"
            ja_existe = Transacao.objects.filter(
                espaco=espaco, data=vencimento, observacao__contains=marcador
            ).exists()
            if ja_existe:
                continue

            Transacao.objects.create(
                espaco=espaco,
                tipo=regra.tipo,
                valor=regra.valor,
                descricao=regra.descricao,
                data=vencimento,
                categoria=regra.categoria,
                conta=regra.conta,
                pago=False,
                prevista=True,
                origem=Origem.RECORRENTE,
                autor=regra.autor,
                compartilhada=regra.compartilhada,
                observacao=marcador,
            )
            criadas += 1

    return criadas


# ---------------------------------------------------------------------------
# Acerto de contas
# ---------------------------------------------------------------------------


def acerto_do_periodo(espaco, inicio: date, fim: date) -> dict:
    """Quem pagou quanto × quanto coube a cada um.

    Sem isto, dividir não serve para nada: as pessoas repartem o custo e nunca
    descobrem quem está devendo a quem.

    Considera só o que é COMPARTILHADO. Gasto pessoal é de quem gastou por
    definição, e entraria dos dois lados da conta sem mudar nada — além de
    expor, pelo saldo, um lançamento que o outro não pode ver.
    """
    from .models import Rateio

    membros = list(espaco.membros.order_by("pk"))
    if len(membros) < 2:
        return {"membros": [], "linhas": [], "sugestao": None}

    despesas = Transacao.objects.filter(
        espaco=espaco,
        data__gte=inicio,
        data__lte=fim,
        tipo=TipoTransacao.DESPESA,
        compartilhada=True,
        prevista=False,
    )

    pagou = {
        linha["pago_por"]: linha["total"]
        for linha in despesas.values("pago_por").annotate(total=Sum("valor"))
    }
    coube = {
        linha["pessoa"]: linha["total"]
        for linha in Rateio.objects.filter(transacao__in=despesas)
        .values("pessoa")
        .annotate(total=Sum("valor"))
    }

    linhas = []
    for pessoa in membros:
        p = pagou.get(pessoa.pk) or Decimal("0")
        c = coube.get(pessoa.pk) or Decimal("0")
        linhas.append({"pessoa": pessoa, "pagou": p, "coube": c, "saldo": p - c})

    # Com duas pessoas, o acerto é uma frase. Com mais, a lista já diz quem
    # está no positivo e quem está no negativo, e fechar isso em transferências
    # mínimas é outro problema — que não vale complicar antes de existir.
    sugestao = None
    if len(linhas) == 2:
        credor = max(linhas, key=lambda linha: linha["saldo"])
        devedor = min(linhas, key=lambda linha: linha["saldo"])
        if credor["saldo"] > 0:
            sugestao = {
                "de": devedor["pessoa"],
                "para": credor["pessoa"],
                "valor": credor["saldo"],
            }

    return {"membros": membros, "linhas": linhas, "sugestao": sugestao}
