"""Orçamento (limites) e planejamento (recorrentes e saldo previsto)."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from accounts.models import Espaco
from carteira import services
from carteira.models import TipoTransacao, Transacao
from carteira.seeds import semear_categorias


class BaseTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)


class LimiteTest(BaseTest):
    def test_consumo_calcula_percentual_e_restante(self):
        limite = services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        services.registrar_transacao(
            espaco=self.espaco, valor="210", descricao="iFood", categoria="Delivery"
        )
        c = services.consumo_do_limite(limite)
        self.assertEqual(c["gasto"], Decimal("210.00"))
        self.assertEqual(c["restante"], Decimal("90.00"))
        self.assertEqual(c["percentual"], 70)
        self.assertFalse(c["estourado"])

    def test_estouro(self):
        limite = services.criar_limite(espaco=self.espaco, valor="100", categoria="Delivery")
        services.registrar_transacao(
            espaco=self.espaco, valor="150", descricao="iFood", categoria="Delivery"
        )
        self.assertTrue(services.consumo_do_limite(limite)["estourado"])

    def test_limite_de_outra_categoria_nao_conta(self):
        limite = services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        services.registrar_transacao(
            espaco=self.espaco, valor="900", descricao="Mercado", categoria="Mercado"
        )
        self.assertEqual(services.consumo_do_limite(limite)["gasto"], Decimal("0"))

    def test_limite_geral_conta_tudo(self):
        limite = services.criar_limite(espaco=self.espaco, valor="5000", rotulo="teto do mês")
        services.registrar_transacao(
            espaco=self.espaco, valor="900", descricao="Mercado", categoria="Mercado"
        )
        services.registrar_transacao(
            espaco=self.espaco, valor="100", descricao="iFood", categoria="Delivery"
        )
        self.assertEqual(services.consumo_do_limite(limite)["gasto"], Decimal("1000.00"))

    def test_limite_temporario_conta_so_a_partir_da_criacao(self):
        # O presente de aniversário não pode comer o orçamento de mercado, nem
        # herdar o que já tinha sido gasto antes de ele existir.
        ontem = date.today() - timedelta(days=1)
        services.registrar_transacao(
            espaco=self.espaco,
            valor="80",
            descricao="Presente antigo",
            categoria="Presentes",
            data_lancamento=ontem,
        )
        limite = services.criar_limite(
            espaco=self.espaco, valor="200", categoria="Presentes", dias=10
        )
        services.registrar_transacao(
            espaco=self.espaco, valor="50", descricao="Presente novo", categoria="Presentes"
        )
        c = services.consumo_do_limite(limite)
        self.assertTrue(limite.temporario)
        self.assertEqual(c["gasto"], Decimal("50.00"))

    def test_previstas_nao_contam_no_consumo(self):
        # O limite mede o que JÁ saiu; a projeção é outro número.
        limite = services.criar_limite(espaco=self.espaco, valor="300", categoria="Moradia")
        regra = services.criar_recorrente(
            espaco=self.espaco,
            descricao="Aluguel",
            valor="250",
            dia_do_mes=5,
            categoria="Moradia",
        )
        regra.inicio = date(2020, 1, 1)
        regra.save(update_fields=["inicio"])
        services.projetar_recorrentes(self.espaco, meses=1)
        self.assertEqual(services.consumo_do_limite(limite)["gasto"], Decimal("0"))


class RecorrenteTest(BaseTest):
    def _regra_antiga(self, **kwargs):
        """Regra que já existia antes do mês corrente.

        Sem isso, um vencimento anterior à criação da regra é pulado de
        propósito, e é justamente o que o teste seguinte cobre.
        """
        regra = services.criar_recorrente(espaco=self.espaco, **kwargs)
        regra.inicio = date(2020, 1, 1)
        regra.save(update_fields=["inicio"])
        return regra

    def test_projecao_cria_previstas(self):
        self._regra_antiga(descricao="Aluguel", valor="1800", dia_do_mes=5, categoria="Moradia")
        criadas = services.projetar_recorrentes(self.espaco, meses=1)
        self.assertEqual(criadas, 1)
        prevista = Transacao.objects.get(prevista=True)
        self.assertEqual(prevista.valor, Decimal("1800.00"))
        self.assertFalse(prevista.pago)

    def test_vencimento_anterior_ao_inicio_da_regra_e_pulado(self):
        # "Aluguel todo dia 5" cadastrado no dia 20 não inventa um previsto
        # para o dia 5 que já passou.
        regra = services.criar_recorrente(
            espaco=self.espaco, descricao="Aluguel", valor="1800", dia_do_mes=5
        )
        regra.inicio = date(2026, 9, 20)
        regra.save(update_fields=["inicio"])
        services.projetar_recorrentes(self.espaco, referencia=date(2026, 9, 25), meses=1)
        self.assertFalse(Transacao.objects.filter(prevista=True).exists())

    def test_projeta_janela_de_varios_meses(self):
        self._regra_antiga(descricao="Aluguel", valor="1800", dia_do_mes=5)
        self.assertEqual(
            services.projetar_recorrentes(self.espaco, referencia=date(2026, 9, 1), meses=3), 3
        )

    def test_projecao_e_idempotente(self):
        # Roda diariamente e a cada consulta de planejamento: rodar duas vezes
        # não pode duplicar.
        self._regra_antiga(descricao="Aluguel", valor="1800", dia_do_mes=5)
        services.projetar_recorrentes(self.espaco, meses=1)
        self.assertEqual(services.projetar_recorrentes(self.espaco, meses=1), 0)
        self.assertEqual(Transacao.objects.filter(prevista=True).count(), 1)

    def test_dia_31_projeta_no_ultimo_dia_de_mes_curto(self):
        self._regra_antiga(descricao="Aluguel", valor="100", dia_do_mes=31)
        services.projetar_recorrentes(self.espaco, referencia=date(2026, 4, 15), meses=1)
        self.assertEqual(Transacao.objects.get(prevista=True).data, date(2026, 4, 30))

    def test_recorrente_encerrado_nao_projeta(self):
        regra = services.criar_recorrente(
            espaco=self.espaco, descricao="Curso", valor="200", dia_do_mes=10
        )
        regra.fim = date.today() - timedelta(days=60)
        regra.save(update_fields=["fim"])
        self.assertEqual(services.projetar_recorrentes(self.espaco, meses=1), 0)

    def test_recorrente_futuro_nao_projeta_antes_do_inicio(self):
        regra = services.criar_recorrente(
            espaco=self.espaco, descricao="Academia", valor="120", dia_do_mes=10
        )
        regra.inicio = date.today() + timedelta(days=90)
        regra.save(update_fields=["inicio"])
        self.assertEqual(services.projetar_recorrentes(self.espaco, meses=1), 0)

    def test_recusa_dia_invalido(self):
        for dia in (0, 32):
            with self.subTest(dia=dia), self.assertRaises(services.ErroDeDominio):
                services.criar_recorrente(
                    espaco=self.espaco, descricao="x", valor="1", dia_do_mes=dia
                )


class SaldoPrevistoTest(BaseTest):
    def test_separa_realizado_de_previsto(self):
        hoje = date.today()
        services.registrar_transacao(
            espaco=self.espaco,
            valor="4200",
            descricao="Salário",
            tipo=TipoTransacao.RECEITA,
            data_lancamento=hoje,
        )
        regra = services.criar_recorrente(
            espaco=self.espaco,
            descricao="Aluguel",
            valor="1800",
            dia_do_mes=min(28, hoje.day),
            categoria="Moradia",
        )
        regra.inicio = date(2020, 1, 1)
        regra.save(update_fields=["inicio"])
        services.projetar_recorrentes(self.espaco, meses=1)

        s = services.saldo_previsto(self.espaco)
        self.assertEqual(s["saldo_realizado"], Decimal("4200.00"))
        self.assertEqual(s["saldo_previsto"], Decimal("2400.00"))
        self.assertEqual(s["a_pagar"], Decimal("1800.00"))


class SaldoDaContaTest(BaseTest):
    def test_saldo_e_calculado_a_partir_das_transacoes(self):
        from carteira.models import Conta

        conta = Conta.objects.create(
            espaco=self.espaco, nome="Nubank", saldo_inicial=Decimal("1000")
        )
        services.registrar_transacao(
            espaco=self.espaco, valor="200", descricao="Mercado", conta="Nubank"
        )
        services.registrar_transacao(
            espaco=self.espaco,
            valor="500",
            descricao="Freela",
            tipo=TipoTransacao.RECEITA,
            conta="Nubank",
        )
        self.assertEqual(services.saldo_da_conta(conta), Decimal("1300.00"))

    def test_prevista_nao_entra_no_saldo(self):
        from carteira.models import Conta

        conta = Conta.objects.create(espaco=self.espaco, nome="Itaú", saldo_inicial=Decimal("500"))
        regra = services.criar_recorrente(
            espaco=self.espaco,
            descricao="Aluguel",
            valor="300",
            dia_do_mes=5,
            conta="Itaú",
        )
        regra.inicio = date(2020, 1, 1)
        regra.save(update_fields=["inicio"])
        services.projetar_recorrentes(self.espaco, meses=1)
        self.assertEqual(services.saldo_da_conta(conta), Decimal("500.00"))
