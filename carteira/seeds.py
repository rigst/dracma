"""Categorias padrão de um espaço novo.

A lista existe para que a primeira mensagem já caia numa categoria razoável.
Sem ela, o agente criaria taxonomia do zero a cada usuário e o relatório do
primeiro mês sairia com vinte categorias de um lançamento cada.

`fixa=True` marca o que é contratado e se repete (aluguel, assinatura), que é o
que alimenta o split fixos × variáveis do relatório.
"""

from .models import Categoria, TipoTransacao

CATEGORIAS_PADRAO = [
    # (nome, emoji, tipo, fixa)
    ("Mercado", "🛒", TipoTransacao.DESPESA, False),
    ("Alimentação", "🍽️", TipoTransacao.DESPESA, False),
    ("Delivery", "🛵", TipoTransacao.DESPESA, False),
    ("Transporte", "🚗", TipoTransacao.DESPESA, False),
    ("Combustível", "⛽", TipoTransacao.DESPESA, False),
    ("Moradia", "🏠", TipoTransacao.DESPESA, True),
    ("Contas de casa", "💡", TipoTransacao.DESPESA, True),
    ("Internet e telefone", "📶", TipoTransacao.DESPESA, True),
    ("Assinaturas", "📺", TipoTransacao.DESPESA, True),
    ("Saúde", "🩺", TipoTransacao.DESPESA, False),
    ("Farmácia", "💊", TipoTransacao.DESPESA, False),
    ("Educação", "📚", TipoTransacao.DESPESA, True),
    ("Lazer", "🎉", TipoTransacao.DESPESA, False),
    ("Vestuário", "👕", TipoTransacao.DESPESA, False),
    ("Cuidados pessoais", "💇", TipoTransacao.DESPESA, False),
    ("Pets", "🐾", TipoTransacao.DESPESA, False),
    ("Presentes", "🎁", TipoTransacao.DESPESA, False),
    ("Viagem", "✈️", TipoTransacao.DESPESA, False),
    ("Impostos e taxas", "🧾", TipoTransacao.DESPESA, True),
    ("Cartão de crédito", "💳", TipoTransacao.DESPESA, False),
    ("Outros", "📦", TipoTransacao.DESPESA, False),
    ("Salário", "💼", TipoTransacao.RECEITA, True),
    ("Freelance", "🧑‍💻", TipoTransacao.RECEITA, False),
    ("Rendimentos", "📈", TipoTransacao.RECEITA, False),
    ("Reembolso", "↩️", TipoTransacao.RECEITA, False),
]


def semear_categorias(espaco) -> int:
    """Idempotente: pode rodar de novo sem duplicar."""
    criadas = 0
    for nome, emoji, tipo, fixa in CATEGORIAS_PADRAO:
        _, nova = Categoria.objects.get_or_create(
            espaco=espaco,
            nome=nome,
            defaults={"emoji": emoji, "tipo": tipo, "fixa": fixa},
        )
        criadas += int(nova)
    return criadas
