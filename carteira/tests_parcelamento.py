"""Compra parcelada: uma parcela por mês, e o mês fechando pelo caixa real.

Nasceu de um caso de produção. "comprei miçangas de 300 reais no cartão em 3x"
virou UMA despesa de R$ 300 no dia, paga — o domínio não tinha parcelamento e
a assistente apenas repetiu "em 3x" no texto. O mês ficou R$ 200 mais pesado
que o caixa e a projeção não sabia das parcelas que ainda vinham.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase

from accounts.models import Espaco, Usuario
from carteira import services
from carteira.models import Transacao
from carteira.seeds import semear_categorias


class BaseParcelaTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )

    def _comprar(self, valor="300", parcelas=3, quando=date(2026, 9, 16), **kwargs):
        return services.registrar_transacao(
            espaco=self.espaco,
            valor=valor,
            descricao="Miçangas",
            categoria="Outros",
            autor=self.usuario,
            data_lancamento=quando,
            parcelas=parcelas,
            **kwargs,
        )

    @property
    def _parcelas(self):
        return list(Transacao.objects.order_by("data"))


class CriacaoTest(BaseParcelaTest):
    def test_tres_lancamentos_um_por_mes(self):
        self._comprar()
        datas = [t.data for t in self._parcelas]
        self.assertEqual(datas, [date(2026, 9, 16), date(2026, 10, 16), date(2026, 11, 16)])

    def test_cada_parcela_vale_a_fatia(self):
        self._comprar()
        self.assertEqual([t.valor for t in self._parcelas], [Decimal("100.00")] * 3)

    def test_a_soma_das_parcelas_e_o_total_da_compra(self):
        # O caso que perde centavo: 100 em 3x.
        self._comprar(valor="100", parcelas=3)
        parcelas = self._parcelas
        self.assertEqual(sum(t.valor for t in parcelas), Decimal("100.00"))
        # A sobra vai na primeira, e não some no arredondamento.
        self.assertEqual(
            [t.valor for t in parcelas],
            [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")],
        )

    def test_so_a_primeira_pode_estar_paga(self):
        # Marcar as futuras como pagas inflaria o saldo da conta com dinheiro
        # que ainda não saiu.
        self._comprar(pago=True)
        self.assertEqual([t.pago for t in self._parcelas], [True, False, False])

    def test_ficam_no_mesmo_grupo_e_numeradas(self):
        self._comprar()
        parcelas = self._parcelas
        self.assertEqual(len({t.grupo_parcela for t in parcelas}), 1)
        self.assertEqual([t.parcela for t in parcelas], [1, 2, 3])
        self.assertEqual([t.total_parcelas for t in parcelas], [3, 3, 3])

    def test_cada_parcela_tem_codigo_proprio(self):
        self._comprar()
        self.assertEqual(len({t.codigo for t in self._parcelas}), 3)

    def test_devolve_a_primeira_parcela(self):
        primeira = self._comprar()
        self.assertEqual(primeira.parcela, 1)
        self.assertEqual(primeira.valor, Decimal("100.00"))

    def test_dia_31_cai_no_ultimo_dia_do_mes_curto(self):
        # Sem isto a parcela de fevereiro simplesmente não existiria.
        self._comprar(quando=date(2026, 1, 31), parcelas=3)
        self.assertEqual(
            [t.data for t in self._parcelas],
            [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)],
        )

    def test_virada_de_ano(self):
        self._comprar(quando=date(2026, 11, 10), parcelas=4)
        self.assertEqual(
            [t.data for t in self._parcelas],
            [date(2026, 11, 10), date(2026, 12, 10), date(2027, 1, 10), date(2027, 2, 10)],
        )


class AVistaTest(BaseParcelaTest):
    def test_uma_parcela_e_um_lancamento_simples(self):
        t = self._comprar(parcelas=1)
        self.assertEqual(Transacao.objects.count(), 1)
        self.assertEqual(t.valor, Decimal("300.00"))
        self.assertFalse(t.parcelada)
        self.assertEqual(t.grupo_parcela, "")

    def test_omitir_parcelas_e_o_mesmo_que_uma(self):
        services.registrar_transacao(
            espaco=self.espaco, valor="50", descricao="Almoço", autor=self.usuario
        )
        self.assertEqual(Transacao.objects.count(), 1)

    def test_zero_e_tratado_como_a_vista(self):
        # Tolerância deliberada: o agente manda `parcelas` ausente ou 0 quando
        # a compra não foi parcelada, e isso não é erro de ninguém.
        t = self._comprar(parcelas=0)
        self.assertEqual(Transacao.objects.count(), 1)
        self.assertFalse(t.parcelada)

    def test_negativo_e_recusado(self):
        with self.assertRaises(services.ErroDeDominio):
            self._comprar(parcelas=-3)

    def test_acima_do_teto_e_recusado(self):
        # Um "1000x" digitado errado encheria a tabela e a projeção por 83 anos.
        with self.assertRaises(services.ErroDeDominio):
            self._comprar(parcelas=services.MAX_PARCELAS + 1)


class MesTest(BaseParcelaTest):
    def test_o_mes_conta_so_a_parcela(self):
        """O ponto todo: R$ 300 em 3x pesa R$ 100 em setembro, não R$ 300."""
        self._comprar()
        inicio, fim = services.limites_do_mes(date(2026, 9, 16))
        resumo = services.resumo_periodo(self.espaco, inicio, fim, usuario=self.usuario)
        self.assertEqual(resumo.despesas, Decimal("100.00"))

    def test_as_futuras_aparecem_nos_meses_seguintes(self):
        self._comprar()
        inicio, fim = services.limites_do_mes(date(2026, 10, 10))
        resumo = services.resumo_periodo(self.espaco, inicio, fim, usuario=self.usuario)
        self.assertEqual(resumo.despesas, Decimal("100.00"))

    def test_o_limite_do_mes_conta_so_a_parcela(self):
        services.criar_limite(espaco=self.espaco, valor="150", categoria="Outros")
        self._comprar()
        limite = self.espaco.limites.get()
        consumo = services.consumo_do_limite(limite, usuario=self.usuario)
        # R$ 100 de 150: não estourou. Com o valor cheio teria estourado.
        self.assertEqual(consumo["gasto"], Decimal("100.00"))
        self.assertFalse(consumo["estourado"])


class RotuloTest(BaseParcelaTest):
    def test_mostra_a_posicao_da_parcela(self):
        self._comprar()
        self.assertEqual([t.rotulo for t in self._parcelas],
                         ["Miçangas (1/3)", "Miçangas (2/3)", "Miçangas (3/3)"])

    def test_a_vista_nao_ganha_sufixo(self):
        t = self._comprar(parcelas=1)
        self.assertEqual(t.rotulo, "Miçangas")
