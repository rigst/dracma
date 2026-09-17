"""A tool `listar_transacoes`: o que ela acha e, sobretudo, o que ela esconde.

Ela existe para a conversa não ter código nenhum: a pessoa diz "o almoço", o
modelo acha aqui e edita pelo código sem nunca mostrá-lo. Isso significa que
ela lê lançamentos individuais, e por isso o recorte de visibilidade é o
teste que mais importa deste arquivo. Um erro aqui não dá tela quebrada, dá
vazamento do gasto pessoal de quem divide o espaço.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Espaco, Usuario
from ai import tools
from ai.agente import Contexto
from carteira.models import Origem
from carteira.seeds import semear_categorias


class BaseListarTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.ana = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        self.bia = Usuario.objects.create_user(
            username="bia", email="bia@exemplo.com", password="x", espaco=self.espaco
        )
        self.hoje = timezone.localdate()

    def _contexto(self, usuario=None):
        return Contexto(espaco=self.espaco, usuario=usuario or self.ana, origem=Origem.TEXTO)

    def _lancar(self, descricao, valor, autor=None, compartilhada=False, dias_atras=0):
        from carteira import services

        return services.registrar_transacao(
            espaco=self.espaco,
            valor=valor,
            descricao=descricao,
            categoria="Alimentação",
            autor=autor or self.ana,
            compartilhada=compartilhada,
            data_lancamento=self.hoje - timedelta(days=dias_atras),
        )

    def _listar(self, usuario=None, **args):
        return tools.executar("listar_transacoes", args, self._contexto(usuario))


class VisibilidadeTest(BaseListarTest):
    def test_nao_mostra_o_gasto_pessoal_de_outra_pessoa(self):
        self._lancar("Presente da Ana", "200", autor=self.bia, compartilhada=False)
        saida = self._listar(usuario=self.ana)

        self.assertNotIn("Presente da Ana", saida)
        self.assertEqual(saida, "Nenhum lançamento encontrado com esses filtros.")

    def test_mostra_o_compartilhado_de_outra_pessoa(self):
        self._lancar("Mercado da casa", "300", autor=self.bia, compartilhada=True)
        self.assertIn("Mercado da casa", self._listar(usuario=self.ana))

    def test_mostra_o_proprio_pessoal(self):
        self._lancar("Almoço", "50", autor=self.ana, compartilhada=False)
        self.assertIn("Almoço", self._listar(usuario=self.ana))

    def test_a_busca_nao_fura_o_recorte(self):
        # Buscar pelo nome exato do lançamento alheio não pode revelá-lo.
        self._lancar("Terapia", "180", autor=self.bia, compartilhada=False)
        self.assertNotIn("Terapia", self._listar(usuario=self.ana, busca="Terapia"))


class BuscaTest(BaseListarTest):
    def test_acha_pela_descricao(self):
        self._lancar("Almoço", "50")
        self._lancar("Uber", "23")
        saida = self._listar(busca="almoço")

        self.assertIn("Almoço", saida)
        self.assertNotIn("Uber", saida)

    def test_busca_ignora_maiusculas(self):
        self._lancar("Mercado Pão de Açúcar", "310")
        self.assertIn("Mercado", self._listar(busca="MERCADO"))

    def test_sem_busca_traz_os_ultimos(self):
        self._lancar("Antigo", "10", dias_atras=20)
        self._lancar("Recente", "20", dias_atras=0)
        saida = self._listar()

        self.assertIn("Antigo", saida)
        self.assertIn("Recente", saida)
        # Mais novo primeiro: "o último" é a referência mais comum.
        self.assertLess(saida.index("Recente"), saida.index("Antigo"))

    def test_filtra_por_periodo(self):
        self._lancar("Antigo", "10", dias_atras=20)
        self._lancar("Recente", "20", dias_atras=0)
        saida = self._listar(inicio=(self.hoje - timedelta(days=5)).isoformat())

        self.assertIn("Recente", saida)
        self.assertNotIn("Antigo", saida)

    def test_nada_encontrado_explica_em_vez_de_voltar_vazio(self):
        self.assertIn("Nenhum lançamento", self._listar(busca="inexistente"))


class LimiteTest(BaseListarTest):
    def test_padrao_traz_dez(self):
        for i in range(15):
            self._lancar(f"Gasto {i}", "10")
        self.assertEqual(len(self._listar().splitlines()), 10)

    def test_teto_rigido_de_trinta(self):
        # O resultado vira contexto do modelo: um "lista tudo" sem teto
        # queimaria a quota da pessoa.
        for i in range(40):
            self._lancar(f"Gasto {i}", "10")
        self.assertEqual(len(self._listar(limite=999).splitlines()), 30)

    def test_limite_zero_ou_negativo_nao_quebra(self):
        for i in range(15):
            self._lancar(f"Gasto {i}", "10")
        # 0 é falsy e cai no padrão; negativo é preso no piso de 1. Nenhum dos
        # dois pode virar fatia vazia nem consulta sem teto.
        self.assertEqual(len(self._listar(limite=0).splitlines()), 10)
        self.assertEqual(len(self._listar(limite=-5).splitlines()), 1)


class SaidaTest(BaseListarTest):
    def test_traz_o_codigo_para_o_modelo_poder_editar(self):
        transacao = self._lancar("Almoço", "50")
        self.assertIn(f"codigo={transacao.codigo}", self._listar())

    def test_traz_o_que_identifica_o_lancamento_para_a_pessoa(self):
        self._lancar("Almoço", "50")
        saida = self._listar()

        self.assertIn("descricao=Almoço", saida)
        self.assertIn("R$ 50,00", saida)
        self.assertIn("categoria=Alimentação", saida)

    def test_data_invalida_volta_como_erro_e_nao_estoura(self):
        self.assertTrue(self._listar(inicio="ontem").startswith("ERRO:"))
