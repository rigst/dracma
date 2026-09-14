"""Tasks proativas: é aqui que o produto deixa de ser um CRUD.

Toda mensagem daqui é INICIADA por nós, e não é resposta a nada — então cada
envio passa por duas guardas:

1. A janela de 24h da Meta (`zap.janela.notificar`), que decide entre texto
   livre, template aprovado ou adiar.
2. O registro em `Alerta`, com unique_together, para o mesmo aviso não sair de
   novo a cada rodada. Sem ele, a verificação horária de limites mandaria "você
   passou de 80%" toda hora até o fim do mês.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Espaco

from . import services
from .models import Alerta, Limite, TipoTransacao, Transacao

logger = logging.getLogger(__name__)


def _dinheiro(valor) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _numeros_do(espaco):
    """Números verificados do espaço. Todo mundo do espaço é avisado: num
    casal, quem estourou o limite de delivery pode não ser quem o criou."""
    from zap.models import NumeroWhatsApp

    return NumeroWhatsApp.objects.filter(
        usuario__espaco=espaco, verificado_em__isnull=False
    ).select_related("usuario")


def _avisar(espaco, tipo: str, chave: str, referencia: str, texto: str, parametros=None) -> bool:
    """Envia uma vez só. Devolve True se de fato saiu para alguém.

    O `Alerta` é criado ANTES do envio: se duas rodadas do beat correrem juntas,
    a segunda bate no IntegrityError e desiste, em vez de mandar em duplicata.
    """
    from zap import janela

    try:
        # `atomic` próprio: sem o savepoint, o IntegrityError capturado deixa a
        # transação externa marcada para rollback e a próxima consulta estoura
        # com TransactionManagementError — no Postgres e no SQLite igualmente.
        with transaction.atomic():
            alerta = Alerta.objects.create(
                espaco=espaco, tipo=tipo, chave=chave, referencia=referencia
            )
    except IntegrityError:
        return False

    template = janela.template_para(tipo)
    enviou = False
    adiou = False

    for numero in _numeros_do(espaco):
        decisao, _ = janela.notificar(
            numero, texto, template=template, parametros=parametros or [texto]
        )
        if decisao is janela.Decisao.ADIAR:
            adiou = True
        else:
            enviou = True

    if not enviou:
        # Sem ninguém alcançado, o alerta fica marcado como adiado — ele conta
        # como "já decidido neste período" e não repete a cada hora, mas o
        # registro diz que a pessoa não foi avisada.
        Alerta.objects.filter(pk=alerta.pk).update(adiado=True)
        logger.info("Alerta %s/%s adiado (janela fechada: %s).", tipo, chave, adiou)

    return enviou


@shared_task
def verificar_limites() -> int:
    """Avisa ao se aproximar e ao estourar cada limite.

    Dois avisos por limite e por período, não mais: um em
    LIMITE_ALERTA_PERCENTUAL e outro no estouro.
    """
    enviados = 0

    for limite in Limite.objects.filter(ativo=True).select_related("espaco", "categoria"):
        consumo = services.consumo_do_limite(limite)
        alvo = limite.categoria.nome if limite.categoria else (limite.rotulo or "geral")
        referencia = f"{consumo['inicio']:%Y-%m}"

        if consumo["estourado"]:
            excedente = consumo["gasto"] - limite.valor
            texto = (
                f"⚠️ Você passou do limite de {alvo}: "
                f"{_dinheiro(consumo['gasto'])} de {_dinheiro(limite.valor)} "
                f"({_dinheiro(excedente)} acima).\n\n"
                "Quer ajustar o limite ou segurar outra categoria pra compensar?"
            )
            if _avisar(
                limite.espaco,
                Alerta.Tipo.LIMITE_ESTOURADO,
                f"limite:{limite.pk}",
                referencia,
                texto,
                [alvo, _dinheiro(consumo["gasto"]), _dinheiro(limite.valor)],
            ):
                enviados += 1

        elif consumo["percentual"] >= settings.LIMITE_ALERTA_PERCENTUAL:
            texto = (
                f"Você já usou {consumo['percentual']}% do limite de {alvo} "
                f"({_dinheiro(consumo['gasto'])} de {_dinheiro(limite.valor)}).\n\n"
                f"Restam {_dinheiro(consumo['restante'])} até {consumo['fim']:%d/%m}."
            )
            if _avisar(
                limite.espaco,
                Alerta.Tipo.LIMITE_PROXIMO,
                f"limite:{limite.pk}",
                referencia,
                texto,
                [alvo, str(consumo["percentual"]), _dinheiro(consumo["restante"])],
            ):
                enviados += 1

    return enviados


@shared_task
def projetar_recorrentes() -> int:
    """Materializa as previstas de todos os espaços."""
    return sum(services.projetar_recorrentes(espaco) for espaco in Espaco.objects.all())


@shared_task
def lembrar_vencimentos() -> int:
    """Avisa das contas que vencem hoje e amanhã.

    Um dia antes E no dia: é o que evita pagar boleto com juros por
    esquecimento, e é o aviso que mais aparece nos relatos de quem usa.
    """
    hoje = timezone.localdate()
    amanha = hoje + timedelta(days=1)
    enviados = 0

    pendentes = (
        Transacao.objects.filter(
            data__in=[hoje, amanha],
            pago=False,
            tipo=TipoTransacao.DESPESA,
        )
        .select_related("espaco", "categoria")
        .order_by("espaco_id", "data")
    )

    for transacao in pendentes:
        quando = "hoje" if transacao.data == hoje else "amanhã"
        texto = (
            f"🔔 {transacao.descricao} vence {quando} "
            f"({transacao.data:%d/%m}): {_dinheiro(transacao.valor)}.\n\n"
            f"Se já pagou, me avisa que eu dou baixa — é só mandar "
            f"“paguei {transacao.codigo}”."
        )
        if _avisar(
            transacao.espaco,
            Alerta.Tipo.VENCIMENTO,
            f"transacao:{transacao.pk}",
            f"{transacao.data:%Y-%m-%d}",
            texto,
            [transacao.descricao, _dinheiro(transacao.valor), quando],
        ):
            enviados += 1

    return enviados


@shared_task
def resumo_semanal() -> int:
    """O caminho do dinheiro dos últimos 7 dias.

    Sem template: é conteúdo, não aviso urgente. Fora da janela de 24h ele é
    simplesmente adiado — mandar por template utility uma mensagem que ninguém
    pediu é o caminho para a Meta reclassificar o número.
    """
    hoje = timezone.localdate()
    inicio = hoje - timedelta(days=7)
    enviados = 0

    for espaco in Espaco.objects.all():
        resumo = services.resumo_periodo(espaco, inicio, hoje)
        if not resumo.despesas and not resumo.receitas:
            continue

        linhas = [
            f"📊 Seus últimos 7 dias ({inicio:%d/%m} a {hoje:%d/%m}):",
            "",
            f"Entrou: {_dinheiro(resumo.receitas)}",
            f"Saiu: {_dinheiro(resumo.despesas)}",
        ]
        maior = resumo.maior_categoria
        if maior:
            nome, valor, percentual = maior
            linhas.append("")
            linhas.append(f"Maior gasto: {nome} — {_dinheiro(valor)} ({percentual}%).")

        if _avisar(
            espaco,
            Alerta.Tipo.RESUMO,
            "semanal",
            f"{hoje:%Y-%m-%d}",
            "\n".join(linhas),
        ):
            enviados += 1

    return enviados
