"""Console web: mesmo agente do WhatsApp, transporte diferente."""

from __future__ import annotations

from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Espaco, Usuario
from ai.fakes import ClienteFalso
from carteira.models import Origem, Transacao
from carteira.seeds import semear_categorias
from zap.models import Mensagem


class BaseConsoleTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        self.client.force_login(self.usuario)
        self.url = reverse("zap:console")

    def _com(self, cliente):
        from ai.agente import responder as real

        def chamada(contexto, conteudo, historico=None):
            return real(contexto, conteudo, historico=historico, cliente=cliente)

        return mock.patch("zap.console.responder", wraps=chamada)


class ConsoleTest(BaseConsoleTest):
    def test_get_mostra_a_tela(self):
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Conversar com a Centavo")

    def test_post_registra_e_devolve_as_duas_falas(self):
        cliente = (
            ClienteFalso()
            .chama(
                "registrar_transacao",
                valor=34,
                descricao="Uber",
                tipo="despesa",
                categoria="Transporte",
                conta="",
                data="2026-09-14",
                pago=True,
            )
            .responde("Registrei: Uber, R$ 34,00 🚗")
        )
        with self._com(cliente):
            resposta = self.client.post(self.url, {"mensagem": "uber 34 reais"})

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "uber 34 reais")
        self.assertContains(resposta, "Registrei")
        # Só o fragmento das falas, não a página inteira.
        self.assertNotContains(resposta, "<html")

        transacao = Transacao.objects.get()
        self.assertEqual(transacao.origem, Origem.PORTAL)
        self.assertEqual(Mensagem.objects.count(), 2)

    def test_mensagem_vazia_nao_chama_a_api(self):
        resposta = self.client.post(self.url, {"mensagem": "   "})
        self.assertEqual(resposta.status_code, 204)
        self.assertEqual(Mensagem.objects.count(), 0)

    def test_falha_do_agente_vira_resposta_amigavel(self):
        with mock.patch("zap.console.responder", side_effect=RuntimeError("boom")):
            resposta = self.client.post(self.url, {"mensagem": "oi"})
        self.assertContains(resposta, "problema")
        self.assertEqual(Mensagem.objects.last().status, Mensagem.Status.ERRO)

    @override_settings(QUOTA_TOKENS_DEFAULT=0)
    def test_sem_quota_explica(self):
        with self._com(ClienteFalso().responde("x")):
            resposta = self.client.post(self.url, {"mensagem": "oi"})
        self.assertContains(resposta, "cota")

    @override_settings(AI_LIMITE_MENSAGENS=2, AI_JANELA_S=60)
    def test_rajada_e_barrada(self):
        cliente = ClienteFalso()
        for _ in range(5):
            cliente.responde("ok")
        with self._com(cliente):
            for _ in range(2):
                self.client.post(self.url, {"mensagem": "oi"})
            resposta = self.client.post(self.url, {"mensagem": "oi"})
        self.assertContains(resposta, "Devagar")

    @override_settings(AI_MAX_CHARS_MENSAGEM=20)
    def test_texto_longo_e_cortado(self):
        cliente = ClienteFalso().responde("ok")
        with self._com(cliente):
            self.client.post(self.url, {"mensagem": "x" * 500})
        self.assertEqual(len(Mensagem.objects.first().texto), 20)

    def test_historico_do_console_ignora_o_whatsapp(self):
        # As duas conversas são do mesmo usuário, mas o console só mostra o que
        # foi dito nele: misturar deixaria a tela com falas que a pessoa mandou
        # de outro lugar.
        from zap.models import NumeroWhatsApp

        numero = NumeroWhatsApp.objects.create(numero="5511999998888", usuario=self.usuario)
        Mensagem.objects.create(
            numero=numero,
            usuario=self.usuario,
            canal="cloud_api",
            direcao=Mensagem.Direcao.ENTRADA,
            texto="veio do zap",
            wamid="w1",
        )
        resposta = self.client.get(self.url)
        self.assertNotContains(resposta, "veio do zap")

    def test_conversa_tem_memoria_curta(self):
        cliente = ClienteFalso().responde("primeira").responde("segunda")
        with self._com(cliente):
            self.client.post(self.url, {"mensagem": "oi"})
            self.client.post(self.url, {"mensagem": "e aí?"})

        papeis = [m["role"] for m in cliente.ultima_chamada["messages"]]
        self.assertEqual(papeis, ["user", "assistant", "user"])
