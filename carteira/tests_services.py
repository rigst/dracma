"""Testes da camada de serviço — o ponto único de escrita do domínio."""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounts.models import Espaco
from carteira import services
from carteira.models import Categoria, Conta, TipoTransacao, Transacao
from carteira.seeds import semear_categorias


class ParaDecimalTest(TestCase):
    """O valor chega de transcrição de áudio e de leitura de comprovante, onde
    o formato varia. Estes são os formatos vistos na prática."""

    def test_aceita_inteiro_e_texto(self):
        self.assertEqual(services.para_decimal(34), Decimal("34.00"))
        self.assertEqual(services.para_decimal("34"), Decimal("34.00"))

    def test_aceita_virgula_decimal_brasileira(self):
        self.assertEqual(services.para_decimal("87,40"), Decimal("87.40"))

    def test_aceita_separador_de_milhar_brasileiro(self):
        self.assertEqual(services.para_decimal("1.234,56"), Decimal("1234.56"))

    def test_aceita_prefixo_de_moeda(self):
        self.assertEqual(services.para_decimal("R$ 19,90"), Decimal("19.90"))

    def test_recusa_valor_nao_numerico(self):
        with self.assertRaises(services.ErroDeDominio):
            services.para_decimal("umas trinta pila")

    def test_recusa_zero_e_negativo(self):
        for entrada in ("0", "-5"):
            with self.subTest(entrada=entrada), self.assertRaises(services.ErroDeDominio):
                services.para_decimal(entrada)


class NormalizarTest(TestCase):
    def test_remove_acento_e_caixa(self):
        self.assertEqual(services.normalizar("Alimentação"), "alimentacao")

    def test_texto_vazio(self):
        self.assertEqual(services.normalizar(""), "")
        self.assertEqual(services.normalizar(None), "")


class DiaValidoTest(TestCase):
    def test_dia_31_em_mes_de_30_cai_no_ultimo(self):
        # Sem isto, um aluguel no dia 31 não seria projetado em abril e o saldo
        # previsto ficaria otimista sem avisar.
        self.assertEqual(services.dia_valido(2026, 4, 31), date(2026, 4, 30))

    def test_fevereiro_bissexto(self):
        self.assertEqual(services.dia_valido(2024, 2, 30), date(2024, 2, 29))

    def test_dia_existente_passa_direto(self):
        self.assertEqual(services.dia_valido(2026, 9, 14), date(2026, 9, 14))


class BaseEspacoTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.conta = Conta.objects.create(espaco=self.espaco, nome="Nubank")


class RegistrarTransacaoTest(BaseEspacoTest):
    def test_registra_com_categoria_e_conta_existentes(self):
        t = services.registrar_transacao(
            espaco=self.espaco,
            valor="34",
            descricao="Uber",
            categoria="Transporte",
            conta="Nubank",
        )
        self.assertEqual(t.valor, Decimal("34.00"))
        self.assertEqual(t.categoria.nome, "Transporte")
        self.assertEqual(t.conta, self.conta)
        self.assertEqual(len(t.codigo), 5)

    def test_casa_categoria_sem_acento(self):
        # A IA escreve "alimentacao"; o banco tem "Alimentação".
        t = services.registrar_transacao(
            espaco=self.espaco, valor="32", descricao="Almoço", categoria="alimentacao"
        )
        self.assertEqual(t.categoria.nome, "Alimentação")
        self.assertEqual(Categoria.objects.filter(espaco=self.espaco).count(), 25)

    def test_cria_categoria_nova_quando_nao_existe(self):
        t = services.registrar_transacao(
            espaco=self.espaco, valor="40", descricao="Banho e tosa", categoria="Pet shop"
        )
        self.assertEqual(t.categoria.nome, "Pet shop")

    def test_conta_inexistente_nao_e_criada(self):
        # Inventar conta por erro de transcrição espalharia saldo por contas
        # fantasma — diferente de categoria, aqui o certo é ficar sem.
        t = services.registrar_transacao(
            espaco=self.espaco, valor="10", descricao="Café", conta="Nubannk Pagamentos"
        )
        self.assertIsNone(t.conta)
        self.assertEqual(Conta.objects.filter(espaco=self.espaco).count(), 1)

    def test_conta_casa_por_correspondencia_parcial(self):
        t = services.registrar_transacao(
            espaco=self.espaco, valor="10", descricao="Café", conta="nubank"
        )
        self.assertEqual(t.conta, self.conta)

    def test_recusa_descricao_vazia(self):
        with self.assertRaises(services.ErroDeDominio):
            services.registrar_transacao(espaco=self.espaco, valor="10", descricao="   ")

    def test_recusa_tipo_desconhecido(self):
        with self.assertRaises(services.ErroDeDominio):
            services.registrar_transacao(
                espaco=self.espaco, valor="10", descricao="x", tipo="estorno"
            )

    def test_codigos_nao_colidem(self):
        codigos = {
            services.registrar_transacao(espaco=self.espaco, valor="1", descricao=f"t{i}").codigo
            for i in range(50)
        }
        self.assertEqual(len(codigos), 50)


class EditarExcluirTest(BaseEspacoTest):
    def setUp(self):
        super().setUp()
        self.t = services.registrar_transacao(
            espaco=self.espaco, valor="34", descricao="Uber", categoria="Transporte"
        )

    def test_edita_valor(self):
        editada = services.editar_transacao(espaco=self.espaco, codigo=self.t.codigo, valor="41,50")
        self.assertEqual(editada.valor, Decimal("41.50"))

    def test_codigo_aceita_minusculo(self):
        editada = services.editar_transacao(
            espaco=self.espaco, codigo=self.t.codigo.lower(), valor="50"
        )
        self.assertEqual(editada.valor, Decimal("50.00"))

    def test_editar_sem_campo_algum_e_erro(self):
        with self.assertRaises(services.ErroDeDominio):
            services.editar_transacao(espaco=self.espaco, codigo=self.t.codigo)

    def test_codigo_inexistente(self):
        with self.assertRaises(services.ErroDeDominio):
            services.editar_transacao(espaco=self.espaco, codigo="ZZZZZ", valor="1")

    def test_codigo_de_outro_espaco_nao_e_acessivel(self):
        outro = Espaco.objects.create(nome="Vizinho")
        with self.assertRaises(services.ErroDeDominio):
            services.editar_transacao(espaco=outro, codigo=self.t.codigo, valor="1")

    def test_exclui(self):
        resumo = services.excluir_transacao(espaco=self.espaco, codigo=self.t.codigo)
        self.assertEqual(resumo["descricao"], "Uber")
        self.assertFalse(Transacao.objects.filter(pk=self.t.pk).exists())


class ResumoTest(BaseEspacoTest):
    def setUp(self):
        super().setUp()
        hoje = date(2026, 9, 10)
        services.registrar_transacao(
            espaco=self.espaco,
            valor="4200",
            descricao="Salário",
            tipo=TipoTransacao.RECEITA,
            categoria="Salário",
            data_lancamento=hoje,
        )
        services.registrar_transacao(
            espaco=self.espaco,
            valor="247,80",
            descricao="Mercado",
            categoria="Mercado",
            data_lancamento=hoje,
        )
        services.registrar_transacao(
            espaco=self.espaco,
            valor="1800",
            descricao="Aluguel",
            categoria="Moradia",
            data_lancamento=hoje,
        )

    def test_soma_receitas_e_despesas(self):
        inicio, fim = services.limites_do_mes(date(2026, 9, 1))
        r = services.resumo_periodo(self.espaco, inicio, fim)
        self.assertEqual(r.receitas, Decimal("4200.00"))
        self.assertEqual(r.despesas, Decimal("2047.80"))
        self.assertEqual(r.saldo, Decimal("2152.20"))

    def test_separa_fixas_de_variaveis(self):
        # Moradia é fixa no seed; Mercado não é.
        inicio, fim = services.limites_do_mes(date(2026, 9, 1))
        r = services.resumo_periodo(self.espaco, inicio, fim)
        self.assertEqual(r.fixas, Decimal("1800.00"))
        self.assertEqual(r.variaveis, Decimal("247.80"))

    def test_maior_categoria(self):
        inicio, fim = services.limites_do_mes(date(2026, 9, 1))
        nome, total, pct = services.resumo_periodo(self.espaco, inicio, fim).maior_categoria
        self.assertIn("Moradia", nome)
        self.assertEqual(total, Decimal("1800.00"))
        self.assertEqual(pct, 87)

    def test_periodo_vazio_nao_estoura(self):
        r = services.resumo_periodo(self.espaco, date(2020, 1, 1), date(2020, 1, 31))
        self.assertEqual(r.despesas, Decimal("0"))
        self.assertIsNone(r.maior_categoria)
