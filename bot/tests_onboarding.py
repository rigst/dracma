"""Onboarding: a tela de conexão, o e-mail e o roteiro no Telegram."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Espaco, Usuario
from bot import onboarding
from bot.canais.fake import FakeCanal
from bot.models import CodigoPareamento, ContaTelegram, Mensagem
from carteira.seeds import semear_categorias

BOT = "DracmaBot"


class BaseOnboardingTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="segredo", espaco=self.espaco
        )
        self.client.force_login(self.usuario)
        self.url = reverse("bot:conectar")


@override_settings(TELEGRAM_BOT_USERNAME=BOT, TELEGRAM_ENABLED=True)
class TelaConectarTest(BaseOnboardingTest):
    def test_exige_login(self):
        self.client.logout()
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/login/", resposta["Location"])

    def test_mostra_codigo_qr_e_link(self):
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)

        codigo = CodigoPareamento.objects.get()
        self.assertContains(resposta, codigo.codigo)
        self.assertContains(resposta, f"t.me/{BOT}?start={codigo.token}")
        # O QR é o que resolve o desktop: a pessoa está no computador e precisa
        # levar o link para o celular.
        self.assertContains(resposta, 'class="qr"')

    def test_recarregar_a_pagina_reaproveita_o_codigo(self):
        # Sem isso, cada recarga inventaria um código novo e o que a pessoa já
        # tinha anotado pararia de funcionar.
        self.client.get(self.url)
        self.client.get(self.url)
        self.assertEqual(CodigoPareamento.objects.count(), 1)

    def test_codigo_expirado_da_lugar_a_um_novo(self):
        velho = CodigoPareamento.objects.create(
            usuario=self.usuario, expira_em=timezone.now() - timedelta(minutes=1)
        )
        self.client.get(self.url)
        self.assertEqual(CodigoPareamento.objects.exclude(pk=velho.pk).count(), 1)

    def test_codigo_ja_usado_da_lugar_a_um_novo(self):
        CodigoPareamento.objects.create(
            usuario=self.usuario,
            usado_em=timezone.now(),
            expira_em=timezone.now() + timedelta(minutes=10),
        )
        self.client.get(self.url)
        self.assertEqual(CodigoPareamento.objects.filter(usado_em__isnull=True).count(), 1)

    def test_conta_conectada_mostra_o_estado_e_nao_gera_codigo(self):
        ContaTelegram.objects.create(
            chat_id=987654321,
            username="ana",
            usuario=self.usuario,
            verificado_em=timezone.now(),
        )
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "@ana")
        self.assertContains(resposta, "Desconectar")
        self.assertEqual(CodigoPareamento.objects.count(), 0)

    @override_settings(TELEGRAM_BOT_USERNAME="")
    def test_sem_bot_configurado_explica_e_aponta_o_console(self):
        # Melhor do que mostrar um QR quebrado.
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "não está configurado")
        self.assertNotContains(resposta, 'class="qr"')
        self.assertContains(resposta, "pelo painel")


@override_settings(TELEGRAM_BOT_USERNAME=BOT, TELEGRAM_ENABLED=True)
class DesconectarTest(BaseOnboardingTest):
    def test_desfaz_o_vinculo_mas_guarda_a_conta(self):
        conta = ContaTelegram.objects.create(
            chat_id=987654321,
            usuario=self.usuario,
            verificado_em=timezone.now(),
            onboarding_etapa=onboarding.CONCLUIDO,
        )
        self.client.post(reverse("bot:desconectar"))

        conta.refresh_from_db()
        self.assertIsNone(conta.usuario)
        self.assertIsNone(conta.verificado_em)
        self.assertEqual(conta.onboarding_etapa, onboarding.NAO_INICIADO)
        # O número guarda o histórico de mensagens; apagá-lo perderia a trilha.
        self.assertTrue(ContaTelegram.objects.filter(pk=conta.pk).exists())

    def test_get_nao_desconecta(self):
        conta = ContaTelegram.objects.create(
            chat_id=987654321, usuario=self.usuario, verificado_em=timezone.now()
        )
        self.assertEqual(self.client.get(reverse("bot:desconectar")).status_code, 405)
        conta.refresh_from_db()
        self.assertEqual(conta.usuario, self.usuario)


@override_settings(
    TELEGRAM_BOT_USERNAME=BOT,
    TELEGRAM_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class EmailInstrucoesTest(BaseOnboardingTest):
    def test_envia_com_codigo_e_link(self):
        resposta = self.client.post(reverse("bot:enviar_instrucoes"), follow=True)
        self.assertEqual(len(mail.outbox), 1)

        enviado = mail.outbox[0]
        self.assertEqual(enviado.to, ["ana@exemplo.com"])
        codigo = CodigoPareamento.objects.get()
        self.assertIn(codigo.codigo, enviado.body)
        self.assertIn(f"t.me/{BOT}?start={codigo.token}", enviado.body)
        self.assertContains(resposta, "Instruções enviadas")

    def test_usa_o_mesmo_codigo_que_a_tela_mostra(self):
        # Um código no e-mail e outro na tela seria uma armadilha silenciosa.
        self.client.get(self.url)
        self.client.post(reverse("bot:enviar_instrucoes"))
        self.assertEqual(CodigoPareamento.objects.count(), 1)

    def test_conta_sem_email_avisa_em_vez_de_falhar(self):
        self.usuario.email = ""
        self.usuario.save(update_fields=["email"])
        resposta = self.client.post(reverse("bot:enviar_instrucoes"), follow=True)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(resposta, "não tem e-mail")

    @override_settings(AI_LIMITE_MENSAGENS=1)
    def test_rajada_de_envios_e_barrada(self):
        for _ in range(5):
            self.client.post(reverse("bot:enviar_instrucoes"))
        self.assertLessEqual(len(mail.outbox), 5)


class RoteiroTest(TestCase):
    """O roteiro de primeiros passos dentro do Telegram."""

    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        self.conta = ContaTelegram.objects.create(
            chat_id=987654321, usuario=self.usuario, verificado_em=timezone.now()
        )
        self.canal = FakeCanal()

    def test_tres_etapas_e_para(self):
        textos = []
        for _ in range(5):
            if onboarding.avancar(self.conta, canal=self.canal):
                textos.append(self.canal.ultimo_texto)

        self.assertEqual(len(textos), 3)
        self.assertIn("conectei", textos[0])
        self.assertIn("limite", textos[1])
        self.assertIn("portal", textos[2])
        self.conta.refresh_from_db()
        self.assertEqual(self.conta.onboarding_etapa, onboarding.CONCLUIDO)

    def test_concluido_nao_manda_mais_nada(self):
        # Mais que três vira spam, e a Meta trata volume não solicitado como
        # sinal de qualidade ruim.
        self.conta.onboarding_etapa = onboarding.CONCLUIDO
        self.conta.save(update_fields=["onboarding_etapa"])
        self.assertFalse(onboarding.avancar(self.conta, canal=self.canal))
        self.assertEqual(self.canal.enviadas, [])

    def test_etapa_avanca_antes_do_envio(self):
        # Se gravasse depois, uma falha de entrega deixaria a pessoa presa
        # recebendo a mesma dica para sempre.
        self.canal.falhar = True
        onboarding.avancar(self.conta, canal=self.canal)
        self.conta.refresh_from_db()
        self.assertEqual(self.conta.onboarding_etapa, onboarding.CONECTADO)

    def test_boas_vindas_ensinam_os_quatro_formatos(self):
        onboarding.avancar(self.conta, canal=self.canal)
        texto = self.canal.ultimo_texto
        for pista in ("áudio", "comprovante", "PDF", "escrevendo"):
            with self.subTest(pista=pista):
                self.assertIn(pista, texto)

    def test_mensagem_de_saida_e_registrada(self):
        onboarding.avancar(self.conta, canal=self.canal)
        saida = Mensagem.objects.get(direcao=Mensagem.Direcao.SAIDA)
        self.assertEqual(saida.status, Mensagem.Status.RESPONDIDA)


class AuxiliaresTest(TestCase):
    @override_settings(TELEGRAM_BOT_USERNAME=BOT)
    def test_deep_link_leva_o_token(self):
        # É a vantagem do Telegram no pareamento: a pessoa toca uma vez e o
        # token chega sozinho no /start, sem digitar nada.
        self.assertEqual(onboarding.link_telegram("tok-abc"), f"https://t.me/{BOT}?start=tok-abc")

    @override_settings(TELEGRAM_BOT_USERNAME="@DracmaBot")
    def test_arroba_no_settings_nao_duplica_no_link(self):
        self.assertEqual(onboarding.link_telegram("tok"), "https://t.me/DracmaBot?start=tok")

    @override_settings(TELEGRAM_BOT_USERNAME="")
    def test_sem_bot_o_link_fica_vazio(self):
        self.assertEqual(onboarding.link_telegram("tok-abc"), "")

    def test_qr_sai_como_svg_sem_cor_fixa_de_fundo(self):
        svg = onboarding.qr_svg("https://exemplo.com")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn('class="qr"', svg)
        self.assertIn('class="qr-linha"', svg)

    @override_settings(SITE_URL="https://dracma.stolben.com")
    def test_convite_aponta_para_a_tela_de_conexao(self):
        texto = onboarding.texto_convite()
        self.assertIn("https://dracma.stolben.com/bot/conectar/", texto)


@override_settings(TELEGRAM_ENABLED=True)
class FaixaNoPainelTest(BaseOnboardingTest):
    def test_quem_nao_conectou_ve_o_convite(self):
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertContains(resposta, "Conecte seu Telegram")

    def test_quem_ja_conectou_nao_ve(self):
        ContaTelegram.objects.create(
            chat_id=987654321, usuario=self.usuario, verificado_em=timezone.now()
        )
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertNotContains(resposta, "Conecte seu Telegram")

    @override_settings(TELEGRAM_ENABLED=False)
    def test_com_telegram_desligado_a_faixa_some(self):
        resposta = self.client.get(reverse("carteira:painel"))
        self.assertNotContains(resposta, "Conecte seu Telegram")


class RoteiroNoFluxoTest(TestCase):
    """O roteiro avança sozinho conforme a pessoa usa."""

    def setUp(self):
        from carteira.seeds import semear_categorias

        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        self.conta = ContaTelegram.objects.create(
            chat_id=987654321,
            usuario=self.usuario,
            verificado_em=timezone.now(),
            onboarding_etapa=onboarding.CONECTADO,
        )
        self.canal = FakeCanal()
        patch = mock.patch("bot.tasks.obter_canal", return_value=self.canal)
        patch.start()
        self.addCleanup(patch.stop)

    def _mensagem(self, texto="uber 34"):
        return Mensagem.objects.create(
            conta=self.conta,
            usuario=self.usuario,
            canal="fake",
            direcao=Mensagem.Direcao.ENTRADA,
            texto=texto,
            id_externo="w1",
        )

    def test_conversa_sem_ferramenta_nao_avanca_o_roteiro(self):
        # Uma dica emendada numa conversa em que nada aconteceu é ruído.
        from ai.agente import Resposta
        from bot.tasks import processar_mensagem

        with mock.patch("bot.tasks.responder", return_value=Resposta(texto="Oi!")):
            processar_mensagem(self._mensagem("oi").pk)

        self.conta.refresh_from_db()
        self.assertEqual(self.conta.onboarding_etapa, onboarding.CONECTADO)

    def test_primeiro_lancamento_dispara_a_dica_de_limite(self):
        from ai.agente import Resposta
        from bot.tasks import processar_mensagem

        with mock.patch(
            "bot.tasks.responder",
            return_value=Resposta(texto="Registrei ✅", ferramentas_usadas=["registrar_transacao"]),
        ):
            processar_mensagem(self._mensagem().pk)

        self.conta.refresh_from_db()
        self.assertEqual(self.conta.onboarding_etapa, onboarding.PRIMEIRO_LANCAMENTO)
        self.assertIn("limite", self.canal.ultimo_texto)
