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
from django.db.models import Q, Sum

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
    compartilhada: bool = True,
    observacao: str = "",
) -> Transacao:
    if tipo not in TipoTransacao.values:
        raise ErroDeDominio(f"Tipo de transação desconhecido: {tipo}.")

    descricao = (descricao or "").strip()[:140]
    if not descricao:
        raise ErroDeDominio("A transação precisa de uma descrição.")

    return Transacao.objects.create(
        espaco=espaco,
        autor=autor,
        tipo=tipo,
        valor=para_decimal(valor),
        descricao=descricao,
        data=data_lancamento or date.today(),
        categoria=achar_categoria(espaco, categoria, tipo),
        conta=achar_conta(espaco, conta),
        pago=pago,
        origem=origem,
        compartilhada=compartilhada,
        observacao=observacao or "",
    )


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


def apenas_compartilhadas(consulta):
    """Só o que é do espaço.

    Usado pelos limites: o alerta vai para todo mundo do espaço, e calcular o
    consumo com gasto pessoal de alguém vazaria esse gasto para os outros pelo
    percentual. Limite é orçamento da casa; o que é pessoal fica de fora, e a
    tela diz isso.
    """
    return consulta.filter(compartilhada=True)


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
    return visiveis_para(consulta, usuario)


def total_gasto(
    espaco,
    inicio: date,
    fim: date,
    categoria=None,
    conta=None,
    incluir_previstas: bool = False,
    usuario=None,
    so_compartilhadas: bool = False,
) -> Decimal:
    consulta = _base(espaco, inicio, fim, incluir_previstas, usuario).filter(
        tipo=TipoTransacao.DESPESA
    )
    if so_compartilhadas:
        consulta = apenas_compartilhadas(consulta)
    if categoria is not None:
        consulta = consulta.filter(categoria=categoria)
    if conta is not None:
        consulta = consulta.filter(conta=conta)
    return consulta.aggregate(total=Sum("valor"))["total"] or Decimal("0")


def resumo_periodo(
    espaco, inicio: date, fim: date, incluir_previstas: bool = False, usuario=None
) -> Resumo:
    consulta = _base(espaco, inicio, fim, incluir_previstas, usuario)

    receitas = consulta.filter(tipo=TipoTransacao.RECEITA).aggregate(t=Sum("valor"))[
        "t"
    ] or Decimal("0")
    despesas = consulta.filter(tipo=TipoTransacao.DESPESA).aggregate(t=Sum("valor"))[
        "t"
    ] or Decimal("0")

    agrupado = (
        consulta.filter(tipo=TipoTransacao.DESPESA)
        .values("categoria__nome", "categoria__emoji")
        .annotate(total=Sum("valor"))
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
        t=Sum("valor")
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


def consumo_do_limite(limite: Limite, referencia: date | None = None) -> dict:
    """Quanto já saiu contra este teto.

    O limite temporário conta desde a criação até o fim; o mensal conta o mês
    corrente. Misturar os dois faria o presente de aniversário estourar o
    orçamento de mercado.
    """
    if limite.temporario:
        inicio, fim = limite.inicio, limite.fim
    else:
        inicio, fim = limites_do_mes(referencia)

    # Só o que é do espaço: ver a justificativa em `apenas_compartilhadas`.
    gasto = total_gasto(
        limite.espaco, inicio, fim, categoria=limite.categoria, so_compartilhadas=True
    )
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
    compartilhada: bool = True,
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
        autor=autor,
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
