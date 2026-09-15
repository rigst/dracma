"""Telas do portal: painel, transações, relatórios, exportação e gráficos."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Espaco, Usuario
from carteira import graficos, services
from carteira.models import Conta, TipoTransacao
from carteira.seeds import semear_categorias


class BasePortalTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.conta = Conta.objects.create(
            espaco=self.espaco, nome="Nubank", saldo_inicial=Decimal("1000")
        )
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="segredo", espaco=self.espaco
        )
        self.client.force_login(self.usuario)

        hoje = timezone.localdate()
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
            conta="Nubank",
            data_lancamento=hoje,
        )
        services.registrar_transacao(
            espaco=self.espaco,
            valor="1800",
            descricao="Aluguel",
            categoria="Moradia",
            data_lancamento=hoje,
        )


class AcessoTest(TestCase):
    def test_portal_exige_login(self):
        for rota in (
            "carteira:painel",
            "carteira:transacoes",
            "carteira:exportar",
            "carteira:nova_transacao",
            "carteira:novo_limite",
            "carteira:novo_recorrente",
            "carteira:nova_conta",
            "zap:console",
        ):
            with self.subTest(rota=rota):
                resposta = self.client.get(reverse(rota))
                self.assertEqual(resposta.status_code, 302)
                self.assertIn("/login/", resposta["Location"])


class PainelTest(BasePortalTest):
    def test_numeros_saem_no_formato_brasileiro(self):
        # 1.800,00 e não 1,800.00 nem o "1,800,00" que o intcomma produzia.
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "4.200,00")
        self.assertContains(resposta, "2.047,80")
        self.assertContains(resposta, "1.800,00")

    def test_mostra_o_saldo_calculado_da_conta(self):
        resposta = self.client.get(reverse("carteira:painel"))
        # 1000 inicial − 247,80 do mercado
        self.assertContains(resposta, "752,20")

    def test_limite_aparece_com_o_consumo(self):
        services.criar_limite(espaco=self.espaco, valor="300", categoria="Mercado")
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertContains(resposta, "82%")

    def test_espaco_vazio_nao_quebra(self):
        vazio = Espaco.objects.create(nome="Novo")
        semear_categorias(vazio)
        novo = Usuario.objects.create_user(
            username="bia", email="bia@exemplo.com", password="x", espaco=vazio
        )
        self.client.force_login(novo)
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Nenhuma despesa")


class TransacoesTest(BasePortalTest):
    def test_lista_tudo_do_mes(self):
        resposta = self.client.get(reverse("carteira:transacoes"), HTTP_HX_REQUEST="true")
        self.assertContains(resposta, "Mercado")
        self.assertContains(resposta, "Aluguel")

    def test_filtra_por_categoria(self):
        # Pela resposta do HTMX: na página inteira o nome da categoria também
        # aparece no <select> do filtro, e a asserção não distinguiria.
        resposta = self.client.get(
            reverse("carteira:transacoes"), {"categoria": "Moradia"}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(resposta, "Aluguel")
        self.assertNotContains(resposta, "Salário")

    def test_filtra_por_tipo(self):
        resposta = self.client.get(
            reverse("carteira:transacoes"), {"tipo": "receita"}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(resposta, "Salário")
        self.assertNotContains(resposta, "Aluguel")

    def test_htmx_devolve_so_a_tabela(self):
        resposta = self.client.get(reverse("carteira:transacoes"), HTTP_HX_REQUEST="true")
        self.assertNotContains(resposta, "<html")
        self.assertContains(resposta, "Mercado")

    def test_painel_lista_os_lancamentos(self):
        self.assertContains(self.client.get(reverse("carteira:painel")), "Mercado")

    def test_nao_enxerga_transacao_de_outro_espaco(self):
        vizinho = Espaco.objects.create(nome="Vizinho")
        semear_categorias(vizinho)
        services.registrar_transacao(
            espaco=vizinho, valor="99", descricao="Segredo do vizinho", categoria="Lazer"
        )
        resposta = self.client.get(reverse("carteira:transacoes"), HTTP_HX_REQUEST="true")
        self.assertNotContains(resposta, "Segredo do vizinho")


class InsightsNoPainelTest(BasePortalTest):
    """Os insights moram no painel: não há tela de relatórios separada."""

    def test_aponta_a_categoria_dominante(self):
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertContains(resposta, "Moradia")
        self.assertContains(resposta, "% das despesas")

    def test_filtro_de_tres_meses_alcanca_lancamento_antigo(self):
        antiga = timezone.localdate() - timedelta(days=45)
        services.registrar_transacao(
            espaco=self.espaco,
            valor="55",
            descricao="Gasto antigo",
            categoria="Lazer",
            data_lancamento=antiga,
        )
        resposta = self.client.get(
            reverse("carteira:transacoes"), {"periodo": "3m"}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(resposta, "Gasto antigo")

    def test_periodo_sem_despesa_tem_insight_proprio(self):
        vazio = Espaco.objects.create(nome="Novo")
        novo = Usuario.objects.create_user(
            username="cris", email="cris@exemplo.com", password="x", espaco=vazio
        )
        self.client.force_login(novo)
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertContains(resposta, "Nenhuma despesa registrada")


class ExportarTest(BasePortalTest):
    def test_csv_tem_cabecalho_e_linhas(self):
        resposta = self.client.get(reverse("carteira:exportar"))
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("text/csv", resposta["Content-Type"])
        self.assertIn("attachment", resposta["Content-Disposition"])

        corpo = resposta.content.decode("utf-8")
        self.assertTrue(corpo.startswith("﻿"))  # BOM para o Excel pt-BR
        self.assertIn("codigo;data;tipo", corpo)
        self.assertIn("Mercado", corpo)
        self.assertIn("247,80", corpo)  # vírgula decimal

    def test_csv_nao_vaza_outro_espaco(self):
        vizinho = Espaco.objects.create(nome="Vizinho")
        semear_categorias(vizinho)
        services.registrar_transacao(
            espaco=vizinho, valor="99", descricao="Segredo do vizinho", categoria="Lazer"
        )
        corpo = self.client.get(reverse("carteira:exportar")).content.decode("utf-8")
        self.assertNotIn("Segredo do vizinho", corpo)


class GraficosTest(TestCase):
    def test_rosca_calcula_percentuais(self):
        fatias = graficos.rosca([("A", Decimal("75")), ("B", Decimal("25"))])
        self.assertEqual([f.percentual for f in fatias], [75, 25])

    def test_fatia_unica_gera_caminho_desenhavel(self):
        # Um arco de exatamente 100% começa e termina no mesmo ponto e não
        # desenha nada; o corte em 99,99% evita a rosca invisível.
        fatia = graficos.rosca([("Só uma", Decimal("10"))])[0]
        self.assertTrue(fatia.caminho.startswith("M "))
        self.assertIn("A 80 80", fatia.caminho)

    def test_agrupa_o_excedente_em_outras(self):
        itens = [(f"c{i}", Decimal(20 - i)) for i in range(10)]
        rotulos = [f.rotulo for f in graficos.rosca(itens)]
        self.assertEqual(len(rotulos), 8)
        self.assertEqual(rotulos[-1], "Outras")

    def test_ignora_valores_nao_positivos(self):
        self.assertEqual(len(graficos.rosca([("A", Decimal("10")), ("B", Decimal("0"))])), 1)

    def test_listas_vazias(self):
        self.assertEqual(graficos.rosca([]), [])
        self.assertEqual(graficos.barras([]), [])

    def test_barras_sao_proporcionais_ao_maior(self):
        barras = graficos.barras([("A", Decimal("100")), ("B", Decimal("50"))])
        self.assertEqual(barras[0].largura, 100)
        self.assertEqual(barras[1].largura, 50)


class PeriodoTest(BasePortalTest):
    def test_periodo_personalizado(self):
        resposta = self.client.get(
            reverse("carteira:transacoes"),
            {"periodo": "custom", "inicio": "2020-01-01", "fim": "2020-01-31"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(resposta, "Nenhum lançamento")

    def test_data_invalida_cai_no_padrao(self):
        resposta = self.client.get(
            reverse("carteira:transacoes"),
            {"periodo": "custom", "inicio": "ontem"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Mercado")


class InsightsTest(TestCase):
    def test_saldo_negativo_e_apontado(self):
        from carteira.views import _insights

        espaco = Espaco.objects.create(nome="X")
        semear_categorias(espaco)
        services.registrar_transacao(
            espaco=espaco,
            valor="500",
            descricao="Compra",
            categoria="Lazer",
            data_lancamento=date.today(),
        )
        inicio, fim = services.limites_do_mes()
        observacoes = _insights(services.resumo_periodo(espaco, inicio, fim))
        self.assertTrue(any("superaram as receitas" in o for o in observacoes))
