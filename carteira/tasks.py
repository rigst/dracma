"""Tasks proativas: é aqui que o produto deixa de ser um CRUD.

Toda mensagem daqui é INICIADA por nós, e não é resposta a nada. No Telegram
isso é simplesmente permitido — some a janela de 24h da Meta, que obrigava
cada envio proativo a escolher entre texto livre, template aprovado e adiar.

Resta uma guarda, e é a que de fato importa: o registro em `Alerta`, com
unique_together, para o mesmo aviso não sair de novo a cada rodada. Sem ele, a
verificação horária de limites mandaria "você passou de 80%" toda hora até o
fim do mês.
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


def _contas_do(espaco, destinatario=None):
    """Conversas alcançáveis a quem o aviso se destina.

    Sem `destinatario`, todo mundo do espaço: num casal, quem estourou o limite
    de delivery pode não ser quem o criou.

    COM `destinatario`, só ele — e é isso que impede um lembrete de conta
    pessoal ("Presente de aniversário vence amanhã, R$ 200") de ser transmitido
    justamente para quem não devia ver aquele lançamento.

    Quem bloqueou o bot fica de fora: o Telegram recusaria com 403 de qualquer
    forma, e sem o filtro seria uma chamada de rede inútil por rodada do beat.
    """
    from bot.models import ContaTelegram

    consulta = ContaTelegram.objects.filter(
        usuario__espaco=espaco, verificado_em__isnull=False, bloqueado_em__isnull=True
    ).select_related("usuario")
    if destinatario is not None:
        consulta = consulta.filter(usuario=destinatario)
    return consulta


def _avisar(
    espaco,
    tipo: str,
    chave: str,
    referencia: str,
    texto: str,
    destinatario=None,
) -> bool:
    """Envia uma vez só. Devolve True se de fato saiu para alguém.

    O `Alerta` é criado ANTES do envio: se duas rodadas do beat correrem juntas,
    a segunda bate no IntegrityError e desiste, em vez de mandar em duplicata.
    """
    from bot import envio

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

    enviou = False

    for conta in _contas_do(espaco, destinatario):
        alcancou, _ = envio.notificar(conta, texto)
        enviou = enviou or alcancou

    if not enviou:
        # Sem ninguém alcançado, o alerta fica marcado como adiado — ele conta
        # como "já decidido neste período" e não repete a cada hora, mas o
        # registro diz que a pessoa não foi avisada.
        Alerta.objects.filter(pk=alerta.pk).update(adiado=True)
        logger.info("Alerta %s/%s não alcançou ninguém; marcado como adiado.", tipo, chave)

    return enviou


@shared_task
def verificar_limites() -> int:
    """Avisa ao se aproximar e ao estourar cada limite.

    Um aviso POR PESSOA, com o número dela. O limite é do espaço, mas o consumo
    é recortado pela visibilidade de quem olha — e mandar a todos o percentual
    calculado com o gasto pessoal de alguém entregaria esse gasto: "você usou
    80% de R$ 400" deixa deduzir que saíram R$ 320.

    Dois avisos por limite, por pessoa e por período: um em
    LIMITE_ALERTA_PERCENTUAL e outro no estouro.
    """
    enviados = 0

    for limite in Limite.objects.filter(ativo=True).select_related("espaco", "categoria"):
        for membro in limite.espaco.membros.all():
            enviados += _avisar_limite(limite, membro)

    return enviados


def _avisar_limite(limite, membro) -> int:
    consumo = services.consumo_do_limite(limite, usuario=membro)
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
        return int(
            _avisar(
                limite.espaco,
                Alerta.Tipo.LIMITE_ESTOURADO,
                f"limite:{limite.pk}:{membro.pk}",
                referencia,
                texto,
                destinatario=membro,
            )
        )

    if consumo["percentual"] >= settings.LIMITE_ALERTA_PERCENTUAL:
        texto = (
            f"Você já usou {consumo['percentual']}% do limite de {alvo} "
            f"({_dinheiro(consumo['gasto'])} de {_dinheiro(limite.valor)}).\n\n"
            f"Restam {_dinheiro(consumo['restante'])} até {consumo['fim']:%d/%m}."
        )
        return int(
            _avisar(
                limite.espaco,
                Alerta.Tipo.LIMITE_PROXIMO,
                f"limite:{limite.pk}:{membro.pk}",
                referencia,
                texto,
                destinatario=membro,
            )
        )

    return 0


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
        .select_related("espaco", "categoria", "autor")
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
            # Conta pessoal só é lembrada a quem a lançou.
            destinatario=None if transacao.compartilhada else transacao.autor,
        ):
            enviados += 1

    return enviados


@shared_task
def resumo_semanal() -> int:
    """O caminho do dinheiro dos últimos 7 dias.

    É conteúdo, não aviso urgente, e no Telegram sai como qualquer outra
    mensagem. A contenção aqui não é da plataforma, é de bom senso: um resumo
    por semana, por pessoa, e o `Alerta` garante que não saia duas vezes.
    """
    hoje = timezone.localdate()
    inicio = hoje - timedelta(days=7)
    enviados = 0

    # Um resumo POR PESSOA, e não um por espaço: o total do espaço somaria o
    # gasto pessoal de cada um e entregaria esse gasto aos outros no Telegram.
    for espaco in Espaco.objects.all():
        for membro in espaco.membros.all():
            resumo = services.resumo_periodo(espaco, inicio, hoje, usuario=membro)
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
                f"semanal:{membro.pk}",
                f"{hoje:%Y-%m-%d}",
                "\n".join(linhas),
                destinatario=membro,
            ):
                enviados += 1

    return enviados
