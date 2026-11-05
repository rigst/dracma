"""A sonda /healthz/ do smoke-test do CD (core/healthz.py)."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.db import OperationalError
from django.test import TestCase, override_settings

from legal.models import DocumentoLegal, TipoDocumento

URL = "/healthz/"
BROKER = "redis://localhost:6379/6"


class HealthzTests(TestCase):
    def test_tudo_no_ar_responde_200_ok(self):
        resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.content, b"ok\n")
        self.assertIn("no-cache", resposta["Cache-Control"])

    def test_banco_fora_responde_503(self):
        with (
            mock.patch("core.healthz.connection.cursor", side_effect=OperationalError("fora")),
            self.assertLogs("core.healthz", level="ERROR") as logs,
        ):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)
        # O motivo vai para o log, não para o corpo da rota pública.
        self.assertEqual(resposta.content, b"indisponivel\n")
        self.assertIn("banco", logs.output[0])

    def test_cache_fora_responde_503(self):
        with (
            mock.patch("core.healthz.cache.set", side_effect=ConnectionError("redis fora")),
            self.assertLogs("core.healthz", level="ERROR") as logs,
        ):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)
        self.assertIn("cache", logs.output[0])

    def test_cache_que_nao_devolve_o_valor_responde_503(self):
        with (
            mock.patch("core.healthz.cache.get", return_value=None),
            self.assertLogs("core.healthz", level="ERROR"),
        ):
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL=BROKER)
    def test_broker_fora_responde_503(self):
        with (
            mock.patch("config.celery.app.connection_for_write") as conexao,
            self.assertLogs("core.healthz", level="ERROR") as logs,
        ):
            conexao.return_value.__enter__.return_value.ensure_connection.side_effect = (
                ConnectionError("broker fora")
            )
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 503)
        self.assertIn("broker", logs.output[0])

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL=BROKER)
    def test_broker_no_ar_responde_200(self):
        with mock.patch("config.celery.app.connection_for_write") as conexao:
            resposta = self.client.get(URL)
        self.assertEqual(resposta.status_code, 200)
        conexao.return_value.__enter__.return_value.ensure_connection.assert_called_once()

    def test_post_nao_e_aceito(self):
        self.assertEqual(self.client.post(URL).status_code, 405)

    def test_nao_passa_pelo_aceite_de_termos(self):
        """Usuário com termos pendentes recebe 200, não o 302 do re-aceite."""
        documento = DocumentoLegal.objects.create(
            tipo=TipoDocumento.TERMOS,
            versao="1.0",
            titulo="Termos",
            corpo_md="# T\n",
            material=True,
        )
        documento.publicar()
        usuario = get_user_model().objects.create_user(username="sonda")
        self.client.force_login(usuario)
        self.assertEqual(self.client.get("/").status_code, 302)  # o resto do app barra
        self.assertEqual(self.client.get(URL).status_code, 200)
