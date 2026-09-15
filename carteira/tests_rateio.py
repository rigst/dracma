"""Dividir o gasto: igual, por proporção, por valor — e o acerto de contas."""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Espaco, Usuario
from carteira import rateios, services
from carteira.models import RateioPadrao, TipoTransacao, Transacao
from carteira.seeds import semear_categorias


class BaseRateioTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.ana = Usuario.objects.create_user(
            username="ana",
            email="ana@exemplo.com",
            password="x",
            first_name="Ana",
            espaco=self.espaco,
        )
        self.bia = Usuario.objects.create_user(
            username="bia",
            email="bia@exemplo.com",
            password="x",
            first_name="Bia",
            espaco=self.espaco,
        )

    def _gasto(self, valor, **extra):
        extra.setdefault("compartilhada", True)
        extra.setdefault("autor", self.ana)
        return services.registrar_transacao(
            espaco=self.espaco, valor=valor, descricao="Conta de luz", **extra
        )


class DivisaoTest(TestCase):
    """A soma das partes tem que bater com o total ao centavo, sempre."""

    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        self.pessoas = [
            Usuario.objects.create_user(
                username=f"p{i}", email=f"p{i}@exemplo.com", password="x", espaco=self.espaco
            )
            for i in range(3)
        ]

    def test_igual_entre_dois(self):
        partes = rateios.dividir_igual(Decimal("310.00"), self.pessoas[:2])
        self.assertEqual(set(partes.values()), {Decimal("155.00")})

    def test_centavo_que_sobra_nao_some_nem_aparece_do_nada(self):
        # R$ 10,00 entre três não dá 3,33 para cada: dá 3,34, 3,33 e 3,33.
        partes = rateios.dividir_igual(Decimal("10.00"), self.pessoas)
        self.assertEqual(sum(partes.values()), Decimal("10.00"))
        self.assertEqual(
            sorted(partes.values()), [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")]
        )

    def test_divisao_e_estavel(self):
        primeira = rateios.dividir_igual(Decimal("10.00"), self.pessoas)
        segunda = rateios.dividir_igual(Decimal("10.00"), self.pessoas)
        self.assertEqual(primeira, segunda)

    def test_por_percentual(self):
        a, b = self.pessoas[0], self.pessoas[1]
        partes = rateios.dividir_por_percentual(
            Decimal("1000.00"), {a: Decimal("60"), b: Decimal("40")}
        )
        self.assertEqual(partes[a], Decimal("600.00"))
        self.assertEqual(partes[b], Decimal("400.00"))

    def test_percentual_que_nao_soma_cem_e_recusado(self):
        a, b = self.pessoas[0], self.pessoas[1]
        with self.assertRaises(rateios.ErroDeRateio) as erro:
            rateios.dividir_por_percentual(Decimal("100.00"), {a: Decimal("60"), b: Decimal("30")})
        self.assertIn("somar 100%", str(erro.exception))

    def test_percentual_com_centavo_que_sobra(self):
        a, b, c = self.pessoas
        partes = rateios.dividir_por_percentual(
            Decimal("100.00"),
            {a: Decimal("33.33"), b: Decimal("33.33"), c: Decimal("33.34")},
        )
        self.assertEqual(sum(partes.values()), Decimal("100.00"))

    def test_por_valor(self):
        a, b = self.pessoas[0], self.pessoas[1]
        partes = rateios.dividir_por_valor(
            Decimal("310.00"), {a: Decimal("200.00"), b: Decimal("110.00")}
        )
        self.assertEqual(partes[a], Decimal("200.00"))

    def test_valores_que_nao_fecham_sao_recusados(self):
        a, b = self.pessoas[0], self.pessoas[1]
        with self.assertRaises(rateios.ErroDeRateio) as erro:
            rateios.dividir_por_valor(
                Decimal("310.00"), {a: Decimal("200.00"), b: Decimal("50.00")}
            )
        self.assertIn("310", str(erro.exception))

    def test_quem_fica_com_zero_nao_vira_linha(self):
        a, b = self.pessoas[0], self.pessoas[1]
        partes = rateios.dividir_por_valor(
            Decimal("100.00"), {a: Decimal("100.00"), b: Decimal("0")}
        )
        self.assertNotIn(b, partes)

    def test_sem_ninguem_para_dividir(self):
        with self.assertRaises(rateios.ErroDeRateio):
            rateios.dividir_igual(Decimal("10.00"), [])


class PadraoDoEspacoTest(BaseRateioTest):
    def test_sem_configurar_divide_igual(self):
        self.assertIsNone(rateios.padrao_do_espaco(self.espaco))
        gasto = self._gasto("310")
        self.assertEqual(
            sorted(gasto.rateios.values_list("valor", flat=True)),
            [Decimal("155.00"), Decimal("155.00")],
        )

    def test_padrao_por_proporcao_vale_para_os_lancamentos_seguintes(self):
        rateios.definir_padrao(self.espaco, {self.ana: Decimal("70"), self.bia: Decimal("30")})
        gasto = self._gasto("1000")
        partes = {r.pessoa: r.valor for r in gasto.rateios.all()}
        self.assertEqual(partes[self.ana], Decimal("700.00"))
        self.assertEqual(partes[self.bia], Decimal("300.00"))

    def test_padrao_pode_voltar_para_igual(self):
        rateios.definir_padrao(self.espaco, {self.ana: Decimal("70"), self.bia: Decimal("30")})
        rateios.definir_padrao(self.espaco, None)
        self.assertIsNone(rateios.padrao_do_espaco(self.espaco))
        self.assertFalse(RateioPadrao.objects.exists())

    def test_padrao_que_nao_soma_cem_e_recusado(self):
        with self.assertRaises(rateios.ErroDeRateio):
            rateios.definir_padrao(self.espaco, {self.ana: Decimal("70"), self.bia: Decimal("10")})

    def test_quem_saiu_do_espaco_nao_recebe_parte(self):
        # O padrão pode ter sido configurado antes de alguém sair.
        rateios.definir_padrao(self.espaco, {self.ana: Decimal("50"), self.bia: Decimal("50")})
        self.bia.espaco = Espaco.objects.create(nome="Outro")
        self.bia.save(update_fields=["espaco"])

        gasto = self._gasto("300")
        self.assertEqual(list(gasto.rateios.values_list("pessoa", flat=True)), [self.ana.pk])
        self.assertEqual(gasto.rateios.get().valor, Decimal("300.00"))

    def test_saida_preserva_a_proporcao_entre_quem_ficou(self):
        cris = Usuario.objects.create_user(
            username="cris", email="cris@exemplo.com", password="x", espaco=self.espaco
        )
        rateios.definir_padrao(
            self.espaco,
            {self.ana: Decimal("70"), self.bia: Decimal("20"), cris: Decimal("10")},
        )
        cris.espaco = Espaco.objects.create(nome="Outro")
        cris.save(update_fields=["espaco"])

        gasto = self._gasto("900")
        partes = {r.pessoa: r.valor for r in gasto.rateios.all()}
        # 70:20 entre os dois → 700 e 200 de 900.
        self.assertEqual(partes[self.ana], Decimal("700.00"))
        self.assertEqual(partes[self.bia], Decimal("200.00"))
        self.assertEqual(sum(partes.values()), Decimal("900.00"))

    def test_quem_entrou_depois_do_padrao_divide_igual(self):
        # Sem isto ficaria de fora de todo lançamento em vez de dividir.
        rateios.definir_padrao(self.espaco, {self.ana: Decimal("100")})
        gasto = self._gasto("300")
        self.assertEqual(gasto.rateios.count(), 2)
        self.assertEqual(
            sorted(gasto.rateios.values_list("valor", flat=True)),
            [Decimal("150.00"), Decimal("150.00")],
        )


class LancamentoTest(BaseRateioTest):
    def test_gasto_pessoal_nao_tem_rateio(self):
        gasto = self._gasto("100", compartilhada=False)
        self.assertEqual(gasto.rateios.count(), 0)

    def test_quem_pagou_e_quem_lancou_podem_ser_diferentes(self):
        gasto = self._gasto("310", autor=self.ana, pago_por=self.bia)
        self.assertEqual(gasto.autor, self.ana)
        self.assertEqual(gasto.pago_por, self.bia)

    def test_quem_pagou_e_quem_lancou_quando_ninguem_diz(self):
        self.assertEqual(self._gasto("310", autor=self.bia).pago_por, self.bia)

    def test_modo_por_valor_no_lancamento(self):
        gasto = self._gasto(
            "310",
            modo_rateio="valor",
            partes={self.ana: Decimal("210.00"), self.bia: Decimal("100.00")},
        )
        partes = {r.pessoa: r.valor for r in gasto.rateios.all()}
        self.assertEqual(partes[self.ana], Decimal("210.00"))

    def test_lancamento_pode_sair_do_padrao_sem_alterar_o_padrao(self):
        rateios.definir_padrao(self.espaco, {self.ana: Decimal("70"), self.bia: Decimal("30")})
        self._gasto("100", modo_rateio="igual")

        seguinte = self._gasto("100")
        partes = {r.pessoa: r.valor for r in seguinte.rateios.all()}
        self.assertEqual(partes[self.ana], Decimal("70.00"))

    def test_reaplicar_troca_o_rateio_em_vez_de_somar(self):
        gasto = self._gasto("300")
        rateios.aplicar(gasto, "valor", {self.ana: Decimal("300.00")})
        self.assertEqual(gasto.rateios.count(), 1)


class TotaisTest(BaseRateioTest):
    """O que cada um vê deixou de ser o valor cheio."""

    def setUp(self):
        super().setUp()
        self.hoje = timezone.localdate()
        self.inicio, self.fim = services.limites_do_mes(self.hoje)

    def _despesas(self, pessoa):
        return services.resumo_periodo(self.espaco, self.inicio, self.fim, usuario=pessoa).despesas

    def test_gasto_dividido_conta_a_metade_para_cada(self):
        self._gasto("310", categoria="Contas de casa")
        self.assertEqual(self._despesas(self.ana), Decimal("155.00"))
        self.assertEqual(self._despesas(self.bia), Decimal("155.00"))

    def test_gasto_pessoal_conta_inteiro_para_quem_lancou(self):
        self._gasto("80", compartilhada=False, autor=self.bia)
        self.assertEqual(self._despesas(self.bia), Decimal("80.00"))
        self.assertEqual(self._despesas(self.ana), Decimal("0"))

    def test_quem_ficou_de_fora_da_divisao_nao_paga_nada(self):
        self._gasto(
            "310",
            modo_rateio="valor",
            partes={self.ana: Decimal("310.00")},
        )
        self.assertEqual(self._despesas(self.ana), Decimal("310.00"))
        self.assertEqual(self._despesas(self.bia), Decimal("0"))

    def test_por_categoria_tambem_usa_a_fatia(self):
        self._gasto("310", categoria="Contas de casa")
        resumo = services.resumo_periodo(self.espaco, self.inicio, self.fim, usuario=self.ana)
        self.assertEqual(resumo.por_categoria[0][1], Decimal("155.00"))

    def test_limite_conta_a_fatia(self):
        limite = services.criar_limite(espaco=self.espaco, valor="200", categoria="Contas de casa")
        self._gasto("310", categoria="Contas de casa")
        consumo = services.consumo_do_limite(limite, usuario=self.ana)
        self.assertEqual(consumo["gasto"], Decimal("155.00"))
        self.assertFalse(consumo["estourado"])

    def test_saldo_da_conta_usa_o_valor_cheio(self):
        # Saldo é caixa: a conta perdeu os R$ 310, não importa como as pessoas
        # dividiram o custo entre si.
        from carteira.models import Conta

        conta = Conta.objects.create(
            espaco=self.espaco, nome="Conjunta", saldo_inicial=Decimal("1000")
        )
        self._gasto("310", conta="Conjunta", categoria="Contas de casa")
        self.assertEqual(services.saldo_da_conta(conta, usuario=self.ana), Decimal("690.00"))


class AcertoTest(BaseRateioTest):
    def setUp(self):
        super().setUp()
        self.inicio, self.fim = services.limites_do_mes()

    def test_quem_pagou_tudo_fica_no_positivo(self):
        self._gasto("310", categoria="Contas de casa", pago_por=self.ana)
        acerto = services.acerto_do_periodo(self.espaco, self.inicio, self.fim)

        por_pessoa = {linha["pessoa"]: linha for linha in acerto["linhas"]}
        self.assertEqual(por_pessoa[self.ana]["pagou"], Decimal("310.00"))
        self.assertEqual(por_pessoa[self.ana]["coube"], Decimal("155.00"))
        self.assertEqual(por_pessoa[self.ana]["saldo"], Decimal("155.00"))
        self.assertEqual(por_pessoa[self.bia]["saldo"], Decimal("-155.00"))

    def test_sugestao_diz_quem_deve_a_quem(self):
        self._gasto("310", categoria="Contas de casa", pago_por=self.ana)
        sugestao = services.acerto_do_periodo(self.espaco, self.inicio, self.fim)["sugestao"]
        self.assertEqual(sugestao["de"], self.bia)
        self.assertEqual(sugestao["para"], self.ana)
        self.assertEqual(sugestao["valor"], Decimal("155.00"))

    def test_pagamentos_equilibrados_nao_geram_divida(self):
        self._gasto("300", categoria="Contas de casa", pago_por=self.ana)
        self._gasto("300", categoria="Mercado", pago_por=self.bia)
        self.assertIsNone(
            services.acerto_do_periodo(self.espaco, self.inicio, self.fim)["sugestao"]
        )

    def test_gasto_pessoal_fica_de_fora_do_acerto(self):
        # Entraria dos dois lados sem mudar nada, e exporia pelo saldo um
        # lançamento que o outro não pode ver.
        self._gasto("500", compartilhada=False, autor=self.ana, pago_por=self.ana)
        acerto = services.acerto_do_periodo(self.espaco, self.inicio, self.fim)
        self.assertEqual(acerto["linhas"][0]["pagou"], Decimal("0"))

    def test_quem_usa_sozinho_nao_tem_acerto(self):
        sozinha = Espaco.objects.create(nome="Só eu")
        Usuario.objects.create_user(
            username="cris", email="cris@exemplo.com", password="x", espaco=sozinha
        )
        acerto = services.acerto_do_periodo(sozinha, self.inicio, self.fim)
        self.assertEqual(acerto["linhas"], [])


class PortalRateioTest(BaseRateioTest):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.ana)

    def _dados(self, **extra):
        base = {
            "tipo": TipoTransacao.DESPESA,
            "valor": "310",
            "descricao": "Conta de luz",
            "data": timezone.localdate().isoformat(),
            "categoria": "",
            "conta": "",
            "pago": "on",
            "compartilhada": "1",
            "modo_rateio": "padrao",
            "pago_por": self.ana.pk,
        }
        base.update(extra)
        return base

    def test_o_dialogo_oferece_os_tres_modos(self):
        resposta = self.client.get(reverse("carteira:nova_transacao"))
        for rotulo in ("Igual entre todos", "Por porcentagem", "Por valor"):
            with self.subTest(rotulo=rotulo):
                self.assertContains(resposta, rotulo)

    def test_lancar_dividindo_igual(self):
        self.client.post(reverse("carteira:nova_transacao"), self._dados(modo_rateio="igual"))
        gasto = Transacao.objects.get(descricao="Conta de luz")
        self.assertEqual(
            sorted(gasto.rateios.values_list("valor", flat=True)),
            [Decimal("155.00"), Decimal("155.00")],
        )

    def test_lancar_por_porcentagem(self):
        self.client.post(
            reverse("carteira:nova_transacao"),
            self._dados(
                modo_rateio="percentual",
                **{f"pct_{self.ana.pk}": "70", f"pct_{self.bia.pk}": "30"},
            ),
        )
        partes = {r.pessoa: r.valor for r in Transacao.objects.get().rateios.all()}
        self.assertEqual(partes[self.ana], Decimal("217.00"))
        self.assertEqual(partes[self.bia], Decimal("93.00"))

    def test_lancar_por_valor(self):
        self.client.post(
            reverse("carteira:nova_transacao"),
            self._dados(
                modo_rateio="valor",
                **{f"val_{self.ana.pk}": "200,00", f"val_{self.bia.pk}": "110,00"},
            ),
        )
        partes = {r.pessoa: r.valor for r in Transacao.objects.get().rateios.all()}
        self.assertEqual(partes[self.ana], Decimal("200.00"))

    def test_porcentagem_que_nao_fecha_volta_para_o_dialogo(self):
        resposta = self.client.post(
            reverse("carteira:nova_transacao"),
            self._dados(
                modo_rateio="percentual",
                **{f"pct_{self.ana.pk}": "70", f"pct_{self.bia.pk}": "10"},
            ),
        )
        self.assertEqual(Transacao.objects.count(), 0)
        self.assertNotIn("HX-Retarget", resposta)
        self.assertContains(resposta, "somar 100%")

    def test_valores_que_nao_fecham_voltam_para_o_dialogo(self):
        resposta = self.client.post(
            reverse("carteira:nova_transacao"),
            self._dados(
                modo_rateio="valor",
                **{f"val_{self.ana.pk}": "200,00", f"val_{self.bia.pk}": "50,00"},
            ),
        )
        self.assertEqual(Transacao.objects.count(), 0)
        self.assertContains(resposta, "310")

    def test_quem_pagou_pode_ser_o_outro(self):
        self.client.post(reverse("carteira:nova_transacao"), self._dados(pago_por=self.bia.pk))
        self.assertEqual(Transacao.objects.get().pago_por, self.bia)

    def test_editar_reabre_com_a_divisao_gravada(self):
        gasto = self._gasto(
            "310",
            modo_rateio="valor",
            partes={self.ana: Decimal("210.00"), self.bia: Decimal("100.00")},
        )
        resposta = self.client.get(reverse("carteira:editar_transacao", args=[gasto.codigo]))
        # Reabre em "por valor" com as partes atuais, em vez de sugerir
        # recalcular pelo padrão e apagar um ajuste feito à mão.
        self.assertContains(resposta, 'value="210.00"')

    def test_editar_pode_mudar_a_divisao(self):
        gasto = self._gasto("310")
        self.client.post(
            reverse("carteira:editar_transacao", args=[gasto.codigo]),
            self._dados(
                modo_rateio="valor",
                **{f"val_{self.ana.pk}": "310,00"},
            ),
        )
        self.assertEqual(gasto.rateios.count(), 1)
        self.assertEqual(gasto.rateios.get().pessoa, self.ana)

    def test_marcar_como_pessoal_apaga_o_rateio(self):
        gasto = self._gasto("310")
        self.assertEqual(gasto.rateios.count(), 2)
        self.client.post(
            reverse("carteira:editar_transacao", args=[gasto.codigo]),
            self._dados(compartilhada="0"),
        )
        self.assertEqual(gasto.rateios.count(), 0)

    def test_a_tabela_mostra_a_fatia_de_quem_olha(self):
        self._gasto("310", categoria="Contas de casa")
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertContains(resposta, "sua parte R$ 155,00")

    def test_o_painel_mostra_o_acerto(self):
        self._gasto("310", categoria="Contas de casa", pago_por=self.ana)
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertContains(resposta, "Acerto do mês")
        self.assertContains(resposta, "R$ 155,00")

    def test_espaco_de_uma_pessoa_nao_tem_divisao(self):
        sozinha = Espaco.objects.create(nome="Só eu")
        semear_categorias(sozinha)
        cris = Usuario.objects.create_user(
            username="cris", email="cris@exemplo.com", password="x", espaco=sozinha
        )
        self.client.force_login(cris)
        resposta = self.client.get(reverse("carteira:nova_transacao"))
        self.assertNotContains(resposta, "Como dividir")
        self.assertNotContains(resposta, "Acerto do mês")


class DivisaoPadraoNaTelaTest(BaseRateioTest):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.ana)

    def test_salvar_porcentagens(self):
        self.client.post(
            reverse("carteira:divisao_padrao"),
            {
                "modo": "percentual",
                f"pct_{self.ana.pk}": "60",
                f"pct_{self.bia.pk}": "40",
            },
        )
        padrao = rateios.padrao_do_espaco(self.espaco)
        self.assertEqual(padrao[self.ana], Decimal("60.00"))

    def test_voltar_para_igual(self):
        rateios.definir_padrao(self.espaco, {self.ana: Decimal("60"), self.bia: Decimal("40")})
        self.client.post(reverse("carteira:divisao_padrao"), {"modo": "igual"})
        self.assertIsNone(rateios.padrao_do_espaco(self.espaco))

    def test_porcentagem_que_nao_fecha_e_recusada(self):
        resposta = self.client.post(
            reverse("carteira:divisao_padrao"),
            {
                "modo": "percentual",
                f"pct_{self.ana.pk}": "60",
                f"pct_{self.bia.pk}": "10",
            },
        )
        self.assertContains(resposta, "somar 100%")
        self.assertFalse(RateioPadrao.objects.exists())

    def test_a_tela_mostra_o_padrao_atual(self):
        rateios.definir_padrao(self.espaco, {self.ana: Decimal("60"), self.bia: Decimal("40")})
        resposta = self.client.get(reverse("carteira:compartilhar"))
        self.assertContains(resposta, "Como dividir, por padrão")
        self.assertContains(resposta, 'value="60.00"')
