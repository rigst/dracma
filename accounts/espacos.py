"""Compartilhar um espaço com alguém.

O espaço é a unidade de convivência: casal, família ou time acompanham o mesmo
mês. Entrar num espaço alheio é a operação mais delicada do app, porque mexe
com dados que já existem dos dois lados — daí este módulo existir separado, com
a mudança inteira dentro de uma transação.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Validade do convite. Curta porque é credencial de acesso a dados
# financeiros de outra pessoa.
VALIDADE_CONVITE = timedelta(days=7)


class ErroDeEspaco(Exception):
    """A mensagem vai para a tela como está."""


def convite_vigente(espaco, criado_por):
    """Convite aberto do espaço, criado se não houver.

    Reaproveitar importa: cada recarga da tela geraria um código novo e o que a
    pessoa já mandou pelo WhatsApp pararia de funcionar.
    """
    from .models import ConviteEspaco

    aberto = (
        ConviteEspaco.objects.filter(
            espaco=espaco, usado_em__isnull=True, expira_em__gt=timezone.now()
        )
        .order_by("-criado_em")
        .first()
    )
    if aberto is not None:
        return aberto

    return ConviteEspaco.objects.create(
        espaco=espaco, criado_por=criado_por, expira_em=timezone.now() + VALIDADE_CONVITE
    )


@transaction.atomic
def entrar_com_codigo(usuario, codigo: str):
    """Move o usuário para o espaço do convite, levando o que é dele junto.

    O que é de fato feito, e por quê:

    - **Os lançamentos vão junto, marcados como PESSOAIS.** Deixá-los para trás
      perderia o histórico da pessoa; trazê-los como compartilhados jogaria o
      extrato inteiro dela na cara de quem convidou. Pessoal é o único padrão
      que não surpreende ninguém.
    - **Contas e categorias são reaproveitadas por nome.** Duas "Nubank" viram
      uma; o que não existe no destino é criado. Sem isso, o espaço ficaria com
      pares duplicados de tudo.
    - **O espaço antigo é apagado** depois da mudança, se ficar sem ninguém.
    """
    from carteira.models import Categoria, Conta, Limite, Recorrente, Transacao

    from .models import ConviteEspaco

    codigo = (codigo or "").strip().upper()
    convite = (
        ConviteEspaco.objects.select_related("espaco")
        .filter(codigo=codigo, usado_em__isnull=True, expira_em__gt=timezone.now())
        .first()
    )
    if convite is None:
        raise ErroDeEspaco("Este código não vale mais. Peça um novo a quem te convidou.")

    destino = convite.espaco
    origem = usuario.espaco

    if origem is not None and origem.pk == destino.pk:
        raise ErroDeEspaco("Você já está neste espaço.")

    if origem is not None:
        _mudar_de_espaco(usuario, origem, destino, Categoria, Conta, Limite, Recorrente, Transacao)

    usuario.espaco = destino
    usuario.save(update_fields=["espaco"])

    convite.usado_em = timezone.now()
    convite.save(update_fields=["usado_em"])

    # O espaço antigo só existia para essa pessoa: sem ninguém, vira órfão com
    # as categorias dentro.
    if origem is not None and not origem.membros.exists():
        origem.delete()

    return destino


def _mudar_de_espaco(usuario, origem, destino, Categoria, Conta, Limite, Recorrente, Transacao):
    from carteira.services import normalizar

    # Mapa de categorias por nome normalizado: "Alimentação" e "alimentacao"
    # são a mesma coisa para quem usa.
    por_nome = {normalizar(c.nome): c for c in Categoria.objects.filter(espaco=destino, ativa=True)}

    def categoria_no_destino(original):
        if original is None:
            return None
        chave = normalizar(original.nome)
        if chave not in por_nome:
            por_nome[chave] = Categoria.objects.create(
                espaco=destino,
                nome=original.nome,
                emoji=original.emoji,
                tipo=original.tipo,
                fixa=original.fixa,
            )
        return por_nome[chave]

    nomes_ocupados = {normalizar(c.nome) for c in Conta.objects.filter(espaco=destino)}
    contas_no_destino = {}
    for conta in Conta.objects.filter(espaco=origem):
        chave = normalizar(conta.nome)
        if chave in nomes_ocupados:
            # Já existe uma conta com esse nome no destino: a da pessoa ganha
            # um sufixo, senão a constraint de nome único derruba a mudança.
            nome = f"{conta.nome} ({usuario.get_short_name() or usuario.username})"[:60]
        else:
            nome = conta.nome
            nomes_ocupados.add(chave)
        contas_no_destino[conta.pk] = Conta.objects.create(
            espaco=destino,
            nome=nome,
            tipo=conta.tipo,
            saldo_inicial=conta.saldo_inicial,
            dia_fechamento=conta.dia_fechamento,
            dia_vencimento=conta.dia_vencimento,
        )

    movidas = 0
    for t in Transacao.objects.filter(espaco=origem).select_related("categoria", "conta"):
        t.espaco = destino
        t.categoria = categoria_no_destino(t.categoria)
        t.conta = contas_no_destino.get(t.conta_id)
        t.conta_destino = contas_no_destino.get(t.conta_destino_id)
        t.autor = t.autor or usuario
        # Pessoal: é o único padrão que não entrega o histórico de alguém a
        # quem acabou de convidá-la.
        t.compartilhada = False
        t.save(
            update_fields=[
                "espaco",
                "categoria",
                "conta",
                "conta_destino",
                "autor",
                "compartilhada",
            ]
        )
        movidas += 1

    for r in Recorrente.objects.filter(espaco=origem).select_related("categoria", "conta"):
        r.espaco = destino
        r.categoria = categoria_no_destino(r.categoria)
        r.conta = contas_no_destino.get(r.conta_id)
        r.autor = r.autor or usuario
        r.compartilhada = False
        r.save(update_fields=["espaco", "categoria", "conta", "autor", "compartilhada"])

    for limite in Limite.objects.filter(espaco=origem).select_related("categoria"):
        limite.espaco = destino
        limite.categoria = categoria_no_destino(limite.categoria)
        limite.save(update_fields=["espaco", "categoria"])

    logger.info("%s mudou de espaço: %s lançamento(s) movido(s).", usuario, movidas)


@transaction.atomic
def sair_do_espaco(usuario):
    """Sai do espaço compartilhado e leva o que é seu para um espaço novo.

    O que é compartilhado FICA: foi lançado para a casa, e quem continua lá
    ainda precisa dele. O que é pessoal vai junto — é da pessoa.
    """
    from carteira.models import Conta, Transacao
    from carteira.seeds import semear_categorias

    from .models import Espaco

    antigo = usuario.espaco
    if antigo is None or antigo.membros.count() <= 1:
        raise ErroDeEspaco("Você não está compartilhando este espaço com ninguém.")

    novo = Espaco.objects.create(nome="Meu espaço")
    semear_categorias(novo)

    usuario.espaco = novo
    usuario.save(update_fields=["espaco"])

    pessoais = Transacao.objects.filter(espaco=antigo, autor=usuario, compartilhada=False)
    if pessoais.exists():
        from carteira.services import normalizar

        por_nome = {normalizar(c.nome): c for c in novo.categorias.all()}
        contas = {}
        for t in pessoais.select_related("categoria", "conta"):
            if t.conta_id and t.conta_id not in contas:
                contas[t.conta_id] = Conta.objects.create(
                    espaco=novo, nome=t.conta.nome, tipo=t.conta.tipo
                )
            t.espaco = novo
            t.categoria = por_nome.get(normalizar(t.categoria.nome)) if t.categoria else None
            t.conta = contas.get(t.conta_id)
            t.conta_destino = None
            t.save(update_fields=["espaco", "categoria", "conta", "conta_destino"])

    return novo
