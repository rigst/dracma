"""
Domínio financeiro: contas, categorias, transações, recorrentes e limites.

Esta camada não conhece o WhatsApp nem a Claude. É a dependência de mão única
que torna o simulador possível e os testes baratos: `zap` e `ai` chamam
`carteira`; `carteira` não importa nenhum dos dois.
"""

from __future__ import annotations

import secrets
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from accounts.models import Espaco

# Base32 de Crockford sem as letras que se confundem com dígitos. O código é
# lido em voz alta e digitado de volta ("edita 0DFPK"), então precisa ser curto
# e sem ambiguidade.
ALFABETO_CODIGO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def gerar_codigo_transacao() -> str:
    """`secrets` e não `random`: `random` em código de produção vira
    VULNERABILITY no Sonar e derruba o gate por new_security_rating."""
    return "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(5))


class TipoTransacao(models.TextChoices):
    DESPESA = "despesa", "Despesa"
    RECEITA = "receita", "Receita"
    TRANSFERENCIA = "transferencia", "Transferência"


class TipoConta(models.TextChoices):
    CORRENTE = "corrente", "Conta corrente"
    POUPANCA = "poupanca", "Poupança"
    CARTAO = "cartao", "Cartão de crédito"
    DINHEIRO = "dinheiro", "Dinheiro"


class Origem(models.TextChoices):
    TEXTO = "texto", "Texto"
    AUDIO = "audio", "Áudio"
    IMAGEM = "imagem", "Imagem"
    PDF = "pdf", "PDF"
    PORTAL = "portal", "Portal"
    RECORRENTE = "recorrente", "Recorrente"


class Conta(models.Model):
    espaco = models.ForeignKey(Espaco, on_delete=models.CASCADE, related_name="contas")
    nome = models.CharField("nome", max_length=60)
    tipo = models.CharField("tipo", max_length=20, choices=TipoConta, default=TipoConta.CORRENTE)
    # Saldo informado pela pessoa na criação. O saldo corrente é este mais a
    # soma das transações — calculado, nunca desnormalizado, para não dessincronizar.
    saldo_inicial = models.DecimalField(
        "saldo inicial", max_digits=12, decimal_places=2, default=Decimal("0")
    )
    # Só para cartão: dia em que a fatura fecha. É o que permite avisar "faltam
    # 3 dias pro cartão fechar".
    dia_fechamento = models.PositiveSmallIntegerField("dia de fechamento", null=True, blank=True)
    dia_vencimento = models.PositiveSmallIntegerField("dia de vencimento", null=True, blank=True)
    ativa = models.BooleanField("ativa", default=True)
    criada_em = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        verbose_name = "conta"
        verbose_name_plural = "contas"
        constraints = [
            models.UniqueConstraint(fields=["espaco", "nome"], name="conta_unica_por_espaco")
        ]

    def __str__(self) -> str:
        return self.nome


class Categoria(models.Model):
    espaco = models.ForeignKey(Espaco, on_delete=models.CASCADE, related_name="categorias")
    nome = models.CharField("nome", max_length=60)
    emoji = models.CharField("emoji", max_length=8, blank=True)
    tipo = models.CharField(
        "tipo", max_length=20, choices=TipoTransacao, default=TipoTransacao.DESPESA
    )
    # Distingue aluguel/assinatura (fixo) de mercado/lazer (variável). É o que
    # alimenta o "100% dos gastos foram variáveis no período" do relatório.
    fixa = models.BooleanField("é gasto fixo", default=False)
    ativa = models.BooleanField("ativa", default=True)

    class Meta:
        verbose_name = "categoria"
        verbose_name_plural = "categorias"
        constraints = [
            models.UniqueConstraint(fields=["espaco", "nome"], name="categoria_unica_por_espaco")
        ]

    def __str__(self) -> str:
        return f"{self.emoji} {self.nome}".strip()


class Transacao(models.Model):
    espaco = models.ForeignKey(Espaco, on_delete=models.CASCADE, related_name="transacoes")
    # Quem lançou. A transação é do espaço; isto é só a autoria, para o
    # "quem comprou o quê" do acompanhamento em casal.
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transacoes",
    )
    codigo = models.CharField("código", max_length=8, unique=True, default=gerar_codigo_transacao)
    tipo = models.CharField("tipo", max_length=20, choices=TipoTransacao)
    # Decimal, nunca float: float não representa 0,10 exatamente e o erro
    # acumula ao somar centenas de lançamentos.
    valor = models.DecimalField(
        "valor",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    descricao = models.CharField("descrição", max_length=140)
    data = models.DateField("data", default=timezone.localdate)
    categoria = models.ForeignKey(
        Categoria, on_delete=models.PROTECT, null=True, blank=True, related_name="transacoes"
    )
    conta = models.ForeignKey(
        Conta, on_delete=models.PROTECT, null=True, blank=True, related_name="transacoes"
    )
    # Destino da transferência. Só preenchido quando tipo=TRANSFERENCIA.
    conta_destino = models.ForeignKey(
        Conta,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transferencias_recebidas",
    )
    pago = models.BooleanField("pago", default=True)
    # Compartilhada = todo mundo do espaço vê. Pessoal = só quem lançou.
    #
    # O padrão é PESSOAL. Compartilhar é a escolha ativa, não o contrário:
    # errar para o lado de guardar não custa nada — a pessoa marca de novo —,
    # enquanto errar para o lado de expor não tem desfazer.
    #
    # A regra de visibilidade mora em `services.visiveis_para` e é UMA só —
    # espalhada, o primeiro relatório novo esqueceria dela e vazaria gasto
    # pessoal num total do casal.
    compartilhada = models.BooleanField("compartilhada", default=False)
    # Quem TIROU o dinheiro do bolso. Diferente de `autor`, que é quem digitou:
    # é comum um lançar o que o outro pagou, e sem separar os dois o acerto de
    # contas fica errado.
    pago_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pagamentos",
        verbose_name="pago por",
    )
    origem = models.CharField("origem", max_length=20, choices=Origem, default=Origem.PORTAL)
    # Previstas nascem do recorrente e ainda não aconteceram; entram na
    # projeção do mês, mas não no "já saiu".
    prevista = models.BooleanField("prevista", default=False)
    observacao = models.TextField("observação", blank=True)
    criada_em = models.DateTimeField("criada em", auto_now_add=True)
    atualizada_em = models.DateTimeField("atualizada em", auto_now=True)

    class Meta:
        verbose_name = "transação"
        verbose_name_plural = "transações"
        ordering = ["-data", "-criada_em"]
        indexes = [
            models.Index(fields=["espaco", "data"]),
            models.Index(fields=["espaco", "categoria", "data"]),
            models.Index(fields=["espaco", "compartilhada", "autor"]),
        ]

    def __str__(self) -> str:
        return f"{self.codigo} · {self.descricao} · R$ {self.valor}"

    @property
    def sinal(self) -> Decimal:
        """Quanto esta transação move o saldo da conta de origem."""
        if self.tipo == TipoTransacao.RECEITA:
            return self.valor
        return -self.valor


class Rateio(models.Model):
    """Quanto de um lançamento cabe a cada pessoa.

    Guarda VALOR, não percentual: igual, por proporção e por valor são três
    jeitos de informar a mesma coisa, e converter na entrada evita o arredonda-
    mento acontecer toda vez que alguém abre um relatório — a soma das partes
    tem que bater com o total ao centavo, sempre.

    Um lançamento compartilhado tem rateios que somam o valor cheio. Um pessoal
    não tem nenhum: ele é inteiro de quem lançou.
    """

    transacao = models.ForeignKey(Transacao, on_delete=models.CASCADE, related_name="rateios")
    pessoa = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rateios"
    )
    valor = models.DecimalField("valor", max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "rateio"
        verbose_name_plural = "rateios"
        constraints = [
            models.UniqueConstraint(fields=["transacao", "pessoa"], name="rateio_unico_por_pessoa")
        ]

    def __str__(self) -> str:
        return f"{self.pessoa} · R$ {self.valor}"


class RateioPadrao(models.Model):
    """Como o espaço divide, quando ninguém disser o contrário.

    Existe porque a divisão costuma ser a MESMA quase sempre — meio a meio, ou
    60/40 porque as rendas são diferentes — e escolher de novo a cada mercado
    seria trabalho repetido. Cada lançamento pode sair do padrão sem alterá-lo.

    Sem nenhuma linha, o padrão é dividir igual entre quem está no espaço. É o
    que vale enquanto ninguém configurou nada.
    """

    espaco = models.ForeignKey(
        "accounts.Espaco", on_delete=models.CASCADE, related_name="rateio_padrao"
    )
    pessoa = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rateios_padrao"
    )
    percentual = models.DecimalField("percentual", max_digits=5, decimal_places=2)

    class Meta:
        verbose_name = "divisão padrão"
        verbose_name_plural = "divisão padrão"
        constraints = [
            models.UniqueConstraint(
                fields=["espaco", "pessoa"], name="rateio_padrao_unico_por_pessoa"
            )
        ]

    def __str__(self) -> str:
        return f"{self.pessoa} · {self.percentual}%"


class Recorrente(models.Model):
    """Ganho ou despesa fixa que se repete todo mês.

    Não gera transação na hora: a task `projetar_recorrentes` materializa as
    previstas do mês, e é isso que permite mostrar o saldo previsto de
    fechamento antes de a primeira conta vencer.
    """

    espaco = models.ForeignKey(Espaco, on_delete=models.CASCADE, related_name="recorrentes")
    descricao = models.CharField("descrição", max_length=140)
    tipo = models.CharField("tipo", max_length=20, choices=TipoTransacao)
    valor = models.DecimalField(
        "valor", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    # 1-31. Em mês curto, a projeção usa o último dia disponível.
    dia_do_mes = models.PositiveSmallIntegerField("dia do mês")
    categoria = models.ForeignKey(
        Categoria, on_delete=models.PROTECT, null=True, blank=True, related_name="recorrentes"
    )
    conta = models.ForeignKey(
        Conta, on_delete=models.PROTECT, null=True, blank=True, related_name="recorrentes"
    )
    # O salário de uma pessoa costuma ser dela; o aluguel, da casa. A previsão
    # que cada um vê usa a mesma regra dos lançamentos, e o padrão é o mesmo:
    # pessoal.
    compartilhada = models.BooleanField("compartilhada", default=False)
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorrentes",
    )
    ativo = models.BooleanField("ativo", default=True)
    inicio = models.DateField("início", default=timezone.localdate)
    fim = models.DateField("fim", null=True, blank=True)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "recorrente"
        verbose_name_plural = "recorrentes"
        ordering = ["dia_do_mes"]

    def __str__(self) -> str:
        return f"{self.descricao} · dia {self.dia_do_mes}"


class Limite(models.Model):
    """Teto de gasto acompanhado pela Dracma.

    `categoria` nulo = teto geral do mês. Com `fim` preenchido vira limite
    temporário ("R$ 200 pra presente"), acompanhado à parte do orçamento
    padrão e que some sozinho quando o período passa.
    """

    espaco = models.ForeignKey(Espaco, on_delete=models.CASCADE, related_name="limites")
    categoria = models.ForeignKey(
        Categoria, on_delete=models.CASCADE, null=True, blank=True, related_name="limites"
    )
    rotulo = models.CharField("rótulo", max_length=60, blank=True)
    valor = models.DecimalField(
        "valor", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    inicio = models.DateField("início", null=True, blank=True)
    fim = models.DateField("fim", null=True, blank=True)
    ativo = models.BooleanField("ativo", default=True)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "limite"
        verbose_name_plural = "limites"

    def __str__(self) -> str:
        alvo = self.categoria.nome if self.categoria else (self.rotulo or "geral")
        return f"{alvo} · R$ {self.valor}"

    @property
    def temporario(self) -> bool:
        return self.fim is not None


class Alerta(models.Model):
    """Registro do que já foi avisado.

    Existe só para não repetir: sem isto, a task horária de limites mandaria o
    mesmo "você passou de 80%" a cada hora até o fim do mês.
    """

    class Tipo(models.TextChoices):
        LIMITE_PROXIMO = "limite_proximo", "Limite próximo"
        LIMITE_ESTOURADO = "limite_estourado", "Limite estourado"
        VENCIMENTO = "vencimento", "Vencimento"
        RESUMO = "resumo", "Resumo"

    espaco = models.ForeignKey(Espaco, on_delete=models.CASCADE, related_name="alertas")
    tipo = models.CharField("tipo", max_length=30, choices=Tipo)
    # Identifica o alvo do alerta dentro do período (id do limite, id do
    # recorrente...). Junto com `referencia` fecha a chave de deduplicação.
    chave = models.CharField("chave", max_length=80)
    # Período ao qual o alerta se refere, no formato AAAA-MM ou AAAA-MM-DD.
    referencia = models.CharField("referência", max_length=12)
    enviado_em = models.DateTimeField("enviado em", auto_now_add=True)
    # Quando a janela de 24h estava fechada e não havia template configurado, o
    # alerta é registrado como adiado em vez de enviado.
    adiado = models.BooleanField("adiado", default=False)

    class Meta:
        verbose_name = "alerta"
        verbose_name_plural = "alertas"
        constraints = [
            models.UniqueConstraint(
                fields=["espaco", "tipo", "chave", "referencia"], name="alerta_unico_por_periodo"
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} · {self.chave} · {self.referencia}"
