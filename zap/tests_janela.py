"""A janela de 24h da Meta — a regra mais delicada da integração."""

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Espaco, Usuario
from carteira.models import Alerta
from zap import janela
from zap.canais.fake import FakeCanal
from zap.models import JanelaAtendimento, Mensagem, NumeroWhatsApp


class BaseJanelaTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        self.numero = NumeroWhatsApp.objects.create(
            numero="5511999998888", usuario=self.usuario, verificado_em=timezone.now()
        )
        self.canal = FakeCanal()

    def _inbound_ha(self, **delta):
        JanelaAtendimento.objects.update_or_create(
            numero=self.numero,
            defaults={"ultimo_inbound_em": timezone.now() - timedelta(**delta)},
        )


class DecisaoTest(BaseJanelaTest):
    def test_sem_inbound_algum_a_janela_esta_fechada(self):
        self.assertFalse(janela.janela_aberta(self.numero))

    def test_inbound_recente_abre(self):
        janela.registrar_inbound(self.numero)
        self.assertTrue(janela.janela_aberta(self.numero))
        self.assertIs(janela.decidir(self.numero), janela.Decisao.LIVRE)

    def test_uma_hora_depois_ainda_esta_aberta(self):
        self._inbound_ha(hours=1)
        self.assertIs(janela.decidir(self.numero), janela.Decisao.LIVRE)

    def test_vinte_e_cinco_horas_depois_fecha(self):
        self._inbound_ha(hours=25)
        self.assertFalse(janela.janela_aberta(self.numero))

    def test_janela_fechada_com_template_usa_template(self):
        self._inbound_ha(hours=25)
        self.assertIs(
            janela.decidir(self.numero, template="aviso_limite"), janela.Decisao.TEMPLATE
        )

    def test_janela_fechada_sem_template_adia(self):
        self._inbound_ha(hours=25)
        self.assertIs(janela.decidir(self.numero), janela.Decisao.ADIAR)

    def test_novo_inbound_reabre(self):
        self._inbound_ha(hours=30)
        janela.registrar_inbound(self.numero)
        self.assertTrue(janela.janela_aberta(self.numero))

    @override_settings(WHATSAPP_JANELA_HORAS=1)
    def test_duracao_da_janela_vem_das_settings(self):
        self._inbound_ha(hours=2)
        self.assertFalse(janela.janela_aberta(self.numero))


class NotificarTest(BaseJanelaTest):
    def test_janela_aberta_manda_texto_livre(self):
        janela.registrar_inbound(self.numero)
        decisao, msg = janela.notificar(
            self.numero, "Você já usou 80% do limite de delivery.", canal=self.canal
        )
        self.assertIs(decisao, janela.Decisao.LIVRE)
        self.assertEqual(self.canal.enviadas[-1]["tipo"], "texto")
        self.assertEqual(msg.tipo, Mensagem.Tipo.TEXTO)

    def test_janela_fechada_manda_template(self):
        self._inbound_ha(hours=25)
        decisao, msg = janela.notificar(
            self.numero,
            "Você já usou 80% do limite de delivery.",
            template="aviso_limite",
            parametros=["Delivery", "80"],
            canal=self.canal,
        )
        self.assertIs(decisao, janela.Decisao.TEMPLATE)
        enviada = self.canal.enviadas[-1]
        self.assertEqual(enviada["tipo"], "template")
        self.assertEqual(enviada["template"], "aviso_limite")
        self.assertEqual(enviada["parametros"], ["Delivery", "80"])
        self.assertEqual(msg.tipo, Mensagem.Tipo.TEMPLATE)

    def test_janela_fechada_sem_template_nao_envia_nada(self):
        # Insistir numa entrega que a Meta vai recusar é pior do que adiar:
        # falha repetida faz a Meta desabilitar a subscrição do webhook.
        self._inbound_ha(hours=25)
        decisao, msg = janela.notificar(self.numero, "aviso", canal=self.canal)
        self.assertIs(decisao, janela.Decisao.ADIAR)
        self.assertIsNone(msg)
        self.assertEqual(self.canal.enviadas, [])

    def test_canal_sem_suporte_a_template_adia(self):
        # No console não existe template; fora da janela o certo é adiar, e
        # não fingir que mandou.
        self._inbound_ha(hours=25)
        self.canal.suporta_template = lambda: False
        decisao, _ = janela.notificar(
            self.numero, "aviso", template="aviso_limite", canal=self.canal
        )
        self.assertIs(decisao, janela.Decisao.ADIAR)

    def test_falha_de_entrega_e_registrada(self):
        janela.registrar_inbound(self.numero)
        self.canal.falhar = True
        _, msg = janela.notificar(self.numero, "aviso", canal=self.canal)
        self.assertEqual(msg.status, Mensagem.Status.ERRO)
        self.assertEqual(msg.erro, "falha simulada")


class ResponderTest(BaseJanelaTest):
    def test_responder_grava_mensagem_de_saida(self):
        msg = janela.responder(self.numero, "Registrei ✅", canal=self.canal)
        self.assertEqual(msg.direcao, Mensagem.Direcao.SAIDA)
        self.assertEqual(msg.status, Mensagem.Status.RESPONDIDA)
        self.assertEqual(self.canal.ultimo_texto, "Registrei ✅")


class TemplateParaTest(TestCase):
    @override_settings(
        WHATSAPP_TEMPLATE_LIMITE="aviso_limite", WHATSAPP_TEMPLATE_VENCIMENTO="aviso_conta"
    )
    def test_mapeia_tipo_de_alerta_para_template(self):
        self.assertEqual(janela.template_para(Alerta.Tipo.LIMITE_PROXIMO), "aviso_limite")
        self.assertEqual(janela.template_para(Alerta.Tipo.LIMITE_ESTOURADO), "aviso_limite")
        self.assertEqual(janela.template_para(Alerta.Tipo.VENCIMENTO), "aviso_conta")

    @override_settings(WHATSAPP_TEMPLATE_LIMITE="")
    def test_template_nao_configurado_devolve_vazio(self):
        self.assertEqual(janela.template_para(Alerta.Tipo.LIMITE_PROXIMO), "")

    def test_tipo_sem_template_devolve_vazio(self):
        self.assertEqual(janela.template_para(Alerta.Tipo.RESUMO), "")


class ObterCanalTest(TestCase):
    def test_canal_padrao_vem_das_settings(self):
        from zap.canais import obter_canal

        with override_settings(CANAL_PADRAO="console"):
            self.assertEqual(obter_canal().nome, "console")

    def test_canal_desconhecido_e_erro(self):
        from zap.canais import obter_canal

        with self.assertRaises(ValueError):
            obter_canal("telegrama")

    def test_cloud_api_so_e_importado_quando_pedido(self):
        # O módulo lê credenciais na importação; carregá-lo na suíte, onde elas
        # não existem, quebraria a coleta.
        from zap.canais import obter_canal

        with mock.patch("zap.canais.cloud_api.CloudAPICanal") as falso:
            obter_canal("cloud_api")
            falso.assert_called_once()
