"""Edição pelo portal: diálogos, ações na linha e os campos do formulário."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Espaco, Usuario
from carteira import services
from carteira.forms import LimiteForm, TransacaoForm
from carteira.models import Conta, Limite, Origem, Recorrente, TipoTransacao, Transacao
from carteira.seeds import semear_categorias


class BaseEdicaoTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.conta = Conta.objects.create(espaco=self.espaco, nome="Nubank")
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        self.client.force_login(self.usuario)

    def _dados(self, **extra):
        base = {
            "tipo": TipoTransacao.DESPESA,
            "valor": "42,50",
            "descricao": "Livraria",
            "data": timezone.localdate().isoformat(),
            "categoria": "",
            "conta": "",
            "pago": "on",
            "compartilhada": "1",
        }
        base.update(extra)
        return base


class CamposTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)

    def test_valor_aceita_virgula_como_a_pessoa_digita(self):
        # O campo numérico do HTML recusa a vírgula fora de um locale pt-BR.
        form = TransacaoForm(
            {
                "tipo": TipoTransacao.DESPESA,
                "valor": "1.234,56",
                "descricao": "Mercado",
                "data": "2026-09-14",
                "pago": "on",
                "compartilhada": "1",
            },
            espaco=self.espaco,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["valor"], Decimal("1234.56"))

    def test_valor_invalido_explica_em_portugues(self):
        form = TransacaoForm(
            {
                "tipo": TipoTransacao.DESPESA,
                "valor": "umas trinta pila",
                "descricao": "x",
                "data": "2026-09-14",
            },
            espaco=self.espaco,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Não entendi o valor", str(form.errors["valor"]))

    def test_data_renderiza_em_iso_para_o_campo_nativo(self):
        # O widget padrão formata no locale (dd/mm/aaaa) e o <input type=date>
        # só aceita ISO — o campo aparecia VAZIO mesmo com initial definido, e
        # a validação do navegador travava o envio sem dizer por quê.
        html = str(TransacaoForm(espaco=self.espaco, initial={"data": date(2026, 9, 14)}))
        self.assertIn('value="2026-09-14"', html)
        self.assertIn('type="date"', html)

    def test_data_aceita_iso_e_formato_brasileiro(self):
        for entrada, esperado in (
            ("2026-09-14", date(2026, 9, 14)),
            ("14/09/2026", date(2026, 9, 14)),
        ):
            with self.subTest(entrada=entrada):
                form = TransacaoForm(
                    {
                        "tipo": TipoTransacao.DESPESA,
                        "valor": "10",
                        "descricao": "x",
                        "data": entrada,
                        "pago": "on",
                        "compartilhada": "1",
                    },
                    espaco=self.espaco,
                )
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["data"], esperado)

    def test_data_absurda_e_recusada(self):
        longe = (date.today().replace(year=date.today().year + 3)).isoformat()
        form = TransacaoForm(
            {
                "tipo": TipoTransacao.DESPESA,
                "valor": "10",
                "descricao": "x",
                "data": longe,
                "pago": "on",
            },
            espaco=self.espaco,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Confira o ano", str(form.errors["data"]))

    def test_categorias_e_contas_sao_do_espaco(self):
        vizinho = Espaco.objects.create(nome="Vizinho")
        Conta.objects.create(espaco=vizinho, nome="Conta do vizinho")
        form = TransacaoForm(espaco=self.espaco)
        self.assertNotIn("Conta do vizinho", str(form["conta"]))

    def test_limite_avulso_sem_nome_e_recusado(self):
        # Sem rótulo ele apareceria como "geral" e ficaria indistinguível do
        # teto do mês.
        form = LimiteForm({"valor": "200", "dias": 10}, espaco=self.espaco)
        self.assertFalse(form.is_valid())
        self.assertIn("rotulo", form.errors)


class DialogoTest(BaseEdicaoTest):
    def test_get_devolve_o_formulario_sem_a_pagina(self):
        resposta = self.client.get(reverse("carteira:nova_transacao"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Novo lançamento")
        self.assertNotContains(resposta, "<html")

    def test_post_valido_grava_e_devolve_o_painel(self):
        resposta = self.client.post(reverse("carteira:nova_transacao"), self._dados())
        self.assertEqual(resposta.status_code, 200)

        transacao = Transacao.objects.get()
        self.assertEqual(transacao.valor, Decimal("42.50"))
        self.assertEqual(transacao.origem, Origem.PORTAL)
        self.assertEqual(transacao.autor, self.usuario)

        # O formulário mira o próprio diálogo para que o erro volte para dentro
        # dele; no sucesso o alvo é redirecionado para o painel.
        self.assertEqual(resposta["HX-Retarget"], "#painel-corpo")
        self.assertEqual(resposta["HX-Trigger"], "gravado")
        self.assertContains(resposta, "Livraria")

    def test_post_invalido_devolve_o_formulario_sem_redirecionar_o_alvo(self):
        resposta = self.client.post(
            reverse("carteira:nova_transacao"), self._dados(valor="abacaxi")
        )
        self.assertEqual(Transacao.objects.count(), 0)
        self.assertNotIn("HX-Retarget", resposta)
        self.assertContains(resposta, "Não entendi o valor")

    def test_editar_carrega_os_valores_atuais(self):
        alvo = services.registrar_transacao(
            espaco=self.espaco, valor="34", descricao="Uber", categoria="Transporte"
        )
        resposta = self.client.get(reverse("carteira:editar_transacao", args=[alvo.codigo]))
        self.assertContains(resposta, alvo.codigo)
        self.assertContains(resposta, "Uber")

    def test_editar_grava(self):
        alvo = services.registrar_transacao(
            espaco=self.espaco, valor="34", descricao="Uber", categoria="Transporte"
        )
        self.client.post(
            reverse("carteira:editar_transacao", args=[alvo.codigo]),
            self._dados(valor="41,90", descricao="Uber para a reunião"),
        )
        alvo.refresh_from_db()
        self.assertEqual(alvo.valor, Decimal("41.90"))
        self.assertEqual(alvo.descricao, "Uber para a reunião")

    def test_codigo_minusculo_funciona(self):
        alvo = services.registrar_transacao(espaco=self.espaco, valor="34", descricao="Uber")
        resposta = self.client.get(reverse("carteira:editar_transacao", args=[alvo.codigo.lower()]))
        self.assertEqual(resposta.status_code, 200)

    def test_nao_edita_lancamento_de_outro_espaco(self):
        vizinho = Espaco.objects.create(nome="Vizinho")
        semear_categorias(vizinho)
        alheio = services.registrar_transacao(espaco=vizinho, valor="99", descricao="Segredo")
        resposta = self.client.get(reverse("carteira:editar_transacao", args=[alheio.codigo]))
        self.assertEqual(resposta.status_code, 404)


class AcoesNaLinhaTest(BaseEdicaoTest):
    def test_apagar(self):
        alvo = services.registrar_transacao(espaco=self.espaco, valor="34", descricao="Uber")
        resposta = self.client.post(reverse("carteira:excluir_transacao", args=[alvo.codigo]))
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Transacao.objects.exists())

    def test_apagar_so_por_post(self):
        alvo = services.registrar_transacao(espaco=self.espaco, valor="34", descricao="Uber")
        self.assertEqual(
            self.client.get(reverse("carteira:excluir_transacao", args=[alvo.codigo])).status_code,
            405,
        )
        self.assertTrue(Transacao.objects.exists())

    def test_dar_baixa_numa_conta(self):
        alvo = services.registrar_transacao(
            espaco=self.espaco, valor="743,20", descricao="Cartão", pago=False
        )
        self.client.post(reverse("carteira:alternar_pago", args=[alvo.codigo]))
        alvo.refresh_from_db()
        self.assertTrue(alvo.pago)

    def test_dar_baixa_numa_prevista_a_torna_real(self):
        # Uma previsão paga deixou de ser previsão; se continuasse marcada,
        # seria contada duas vezes na projeção.
        regra = services.criar_recorrente(
            espaco=self.espaco, descricao="Aluguel", valor="1800", dia_do_mes=5
        )
        regra.inicio = date(2020, 1, 1)
        regra.save(update_fields=["inicio"])
        services.projetar_recorrentes(self.espaco, meses=1)
        prevista = Transacao.objects.get(prevista=True)

        self.client.post(reverse("carteira:alternar_pago", args=[prevista.codigo]))
        prevista.refresh_from_db()
        self.assertTrue(prevista.pago)
        self.assertFalse(prevista.prevista)


class LimiteRecorrenteContaTest(BaseEdicaoTest):
    def test_criar_limite(self):
        categoria = self.espaco.categorias.get(nome="Delivery")
        self.client.post(
            reverse("carteira:novo_limite"),
            {"categoria": categoria.pk, "valor": "400", "rotulo": "", "dias": ""},
        )
        limite = Limite.objects.get()
        self.assertEqual(limite.valor, Decimal("400.00"))
        self.assertFalse(limite.temporario)

    def test_criar_limite_avulso(self):
        self.client.post(
            reverse("carteira:novo_limite"),
            {"categoria": "", "valor": "200", "rotulo": "Presente", "dias": "7"},
        )
        limite = Limite.objects.get()
        self.assertTrue(limite.temporario)
        self.assertEqual(limite.fim, date.today() + timedelta(days=7))

    def test_remover_limite(self):
        limite = services.criar_limite(espaco=self.espaco, valor="300", categoria="Delivery")
        self.client.post(reverse("carteira:excluir_limite", args=[limite.pk]))
        self.assertFalse(Limite.objects.exists())

    def test_criar_recorrente_ja_projeta(self):
        self.client.post(
            reverse("carteira:novo_recorrente"),
            {
                "descricao": "Aluguel",
                "tipo": TipoTransacao.DESPESA,
                "valor": "1800",
                "dia_do_mes": "28",
                "categoria": "",
                "conta": "",
                "compartilhada": "1",
            },
        )
        self.assertEqual(Recorrente.objects.count(), 1)
        self.assertTrue(Transacao.objects.filter(prevista=True).exists())

    def test_remover_recorrente(self):
        regra = services.criar_recorrente(
            espaco=self.espaco, descricao="Aluguel", valor="1800", dia_do_mes=5
        )
        self.client.post(reverse("carteira:excluir_recorrente", args=[regra.pk]))
        self.assertFalse(Recorrente.objects.exists())

    def test_criar_conta(self):
        self.client.post(
            reverse("carteira:nova_conta"),
            {"nome": "Itaú", "tipo": "corrente", "saldo_inicial": "1.200,00"},
        )
        conta = Conta.objects.get(nome="Itaú")
        self.assertEqual(conta.saldo_inicial, Decimal("1200.00"))

    def test_conta_sem_saldo_informado_nasce_zerada(self):
        self.client.post(
            reverse("carteira:nova_conta"),
            {"nome": "Carteira", "tipo": "dinheiro", "saldo_inicial": ""},
        )
        self.assertEqual(Conta.objects.get(nome="Carteira").saldo_inicial, Decimal("0"))

    def test_conta_com_nome_repetido_e_recusada(self):
        self.client.post(
            reverse("carteira:nova_conta"),
            {"nome": "nubank", "tipo": "corrente", "saldo_inicial": "0"},
        )
        self.assertEqual(Conta.objects.filter(espaco=self.espaco).count(), 1)


class EspacoGarantidoTest(TestCase):
    def test_conta_sem_espaco_ganha_um_ao_abrir_o_painel(self):
        # Contas criadas fora do cadastro (createsuperuser, por exemplo)
        # chegavam aqui sem espaço e derrubavam o painel com AttributeError.
        usuario = Usuario.objects.create_user(username="sem", email="sem@exemplo.com", password="x")
        self.client.force_login(usuario)
        resposta = self.client.get(reverse("carteira:painel"))

        self.assertEqual(resposta.status_code, 200)
        usuario.refresh_from_db()
        self.assertIsNotNone(usuario.espaco)
        self.assertTrue(usuario.espaco.categorias.exists())
