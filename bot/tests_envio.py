"""Saída de mensagens e o tratamento de quem bloqueou o bot.

Este módulo substitui os testes da janela de 24h da Meta. A regra sumiu com a
plataforma: no Telegram, depois do `/start`, o bot escreve quando quiser. O que
sobrou para testar é o que de fato pode dar errado — a entrega falhar, e a
pessoa bloquear o bot.
"""

from unittest import mock

from django.test import TestCase
from django.utils import timezone

from accounts.models import Espaco, Usuario
from bot import envio
from bot.canais.fake import FakeCanal
from bot.models import ContaTelegram, Mensagem


class BaseEnvioTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        self.conta = ContaTelegram.objects.create(
            chat_id=987654321, usuario=self.usuario, verificado_em=timezone.now()
        )
        self.canal = FakeCanal()


class ResponderTest(BaseEnvioTest):
    def test_grava_mensagem_de_saida(self):
        mensagem = envio.responder(self.conta, "beleza", canal=self.canal)

        self.assertEqual(mensagem.direcao, Mensagem.Direcao.SAIDA)
        self.assertEqual(mensagem.texto, "beleza")
        self.assertEqual(mensagem.conta, self.conta)
        self.assertEqual(mensagem.usuario, self.usuario)
        self.assertEqual(mensagem.status, Mensagem.Status.RESPONDIDA)
        self.assertEqual(self.canal.ultimo_texto, "beleza")

    def test_manda_para_o_chat_id(self):
        envio.responder(self.conta, "oi", canal=self.canal)
        self.assertEqual(self.canal.enviadas[-1]["destino"], "987654321")

    def test_falha_de_entrega_e_registrada(self):
        self.canal.falhar = True
        mensagem = envio.responder(self.conta, "oi", canal=self.canal)

        self.assertEqual(mensagem.status, Mensagem.Status.ERRO)
        self.assertEqual(mensagem.erro, "falha simulada")


class NotificarTest(BaseEnvioTest):
    def test_alcanca_e_registra(self):
        alcancou, mensagem = envio.notificar(self.conta, "você passou do limite", canal=self.canal)

        self.assertTrue(alcancou)
        self.assertEqual(mensagem.texto, "você passou do limite")
        self.assertEqual(self.canal.ultimo_texto, "você passou do limite")

    def test_sem_janela_para_consultar(self):
        # O ponto da migração: um alerta proativo é só uma mensagem. Não há
        # inbound recente nenhum nesta conta e mesmo assim ele sai.
        self.assertFalse(Mensagem.objects.filter(direcao=Mensagem.Direcao.ENTRADA).exists())
        alcancou, _ = envio.notificar(self.conta, "lembrete", canal=self.canal)
        self.assertTrue(alcancou)

    def test_falha_de_entrega_nao_conta_como_alcancado(self):
        self.canal.falhar = True
        alcancou, mensagem = envio.notificar(self.conta, "oi", canal=self.canal)

        self.assertFalse(alcancou)
        self.assertEqual(mensagem.status, Mensagem.Status.ERRO)


class BloqueioTest(BaseEnvioTest):
    def test_403_marca_a_conta(self):
        self.canal.bloquear = True
        envio.responder(self.conta, "oi", canal=self.canal)

        self.conta.refresh_from_db()
        self.assertIsNotNone(self.conta.bloqueado_em)

    def test_conta_bloqueada_nao_gasta_chamada_de_rede(self):
        self.conta.bloqueado_em = timezone.now()
        self.conta.save(update_fields=["bloqueado_em"])

        alcancou, mensagem = envio.notificar(self.conta, "oi", canal=self.canal)

        self.assertFalse(alcancou)
        self.assertIsNone(mensagem)
        self.assertEqual(self.canal.enviadas, [])

    def test_entrega_depois_do_bloqueio_limpa_a_marca(self):
        # A pessoa desbloqueou. Sem isto ela ficaria sem alertas para sempre,
        # e seria preciso mexer no banco à mão para devolvê-los.
        self.conta.bloqueado_em = timezone.now()
        self.conta.save(update_fields=["bloqueado_em"])

        envio.responder(self.conta, "oi", canal=self.canal)

        self.conta.refresh_from_db()
        self.assertIsNone(self.conta.bloqueado_em)

    def test_bloqueio_nao_desfaz_o_vinculo(self):
        # O histórico continua sendo da pessoa, e ela pode desbloquear.
        self.canal.bloquear = True
        envio.responder(self.conta, "oi", canal=self.canal)

        self.conta.refresh_from_db()
        self.assertEqual(self.conta.usuario, self.usuario)
        self.assertIsNotNone(self.conta.verificado_em)


class CanalPadraoTest(BaseEnvioTest):
    def test_sem_canal_explicito_usa_o_configurado(self):
        with mock.patch("bot.envio.obter_canal", return_value=self.canal) as obter:
            envio.responder(self.conta, "oi")
        obter.assert_called_once_with()
        self.assertEqual(self.canal.ultimo_texto, "oi")
