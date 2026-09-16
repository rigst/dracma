"""O canal do Telegram: o que ele repete, o que ele desiste e o que devolve.

A regra que estes testes trancam veio de uma falha real em produção: um
handshake TLS estourou o tempo uma vez, a resposta do agente foi gravada como
erro e a pessoa ficou esperando por algo que nunca ia chegar. Um blip de rede
não pode custar a resposta.
"""

from __future__ import annotations

from unittest import mock

import httpx
from django.test import TestCase, override_settings

from bot.canais.telegram import TelegramCanal, TelegramErro, identificador

SETTINGS = {
    "TELEGRAM_ENABLED": True,
    "TELEGRAM_BOT_TOKEN": "123:ABC",
    "TELEGRAM_API_BASE": "https://api.telegram.org",
    "TELEGRAM_MAX_MIDIA_BYTES": 20 * 1024 * 1024,
}


def resposta(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def ok(message_id: int = 5, chat_id: int = 99) -> httpx.Response:
    return resposta({"ok": True, "result": {"message_id": message_id, "chat": {"id": chat_id}}})


def falha(codigo: int, descricao: str = "nope", **parametros) -> httpx.Response:
    corpo = {"ok": False, "error_code": codigo, "description": descricao}
    if parametros:
        corpo["parameters"] = parametros
    return resposta(corpo, status=codigo)


@override_settings(**SETTINGS)
class RepeticaoTest(TestCase):
    """Sem `sleep` de verdade: o teste não pode pagar o backoff."""

    def setUp(self):
        self.dorme = mock.patch("bot.canais.telegram.time.sleep")
        self.dorme.start()
        self.addCleanup(self.dorme.stop)

    def _canal(self, *respostas):
        cliente = mock.Mock(spec=httpx.Client)
        cliente.post.side_effect = list(respostas)
        return TelegramCanal(cliente=cliente), cliente

    def test_falha_de_rede_e_repetida_e_a_segunda_entrega(self):
        canal, cliente = self._canal(httpx.ConnectTimeout("handshake"), ok())
        resultado = canal.enviar_texto(99, "oi")

        self.assertTrue(resultado.entregue)
        self.assertEqual(cliente.post.call_count, 2)

    def test_handshake_timeout_repetido_ate_entregar(self):
        # É exatamente a falha que aconteceu em produção.
        canal, cliente = self._canal(
            httpx.ConnectTimeout("_ssl.c:983: The handshake operation timed out"), ok()
        )
        self.assertTrue(canal.enviar_texto(99, "oi").entregue)

    def test_rede_falhando_sempre_desiste_depois_de_tres(self):
        canal, cliente = self._canal(
            httpx.ConnectTimeout("x"), httpx.ConnectTimeout("x"), httpx.ConnectTimeout("x")
        )
        resultado = canal.enviar_texto(99, "oi")

        self.assertFalse(resultado.entregue)
        self.assertFalse(resultado.bloqueado)
        self.assertEqual(cliente.post.call_count, 3)

    def test_429_respeita_o_retry_after_do_telegram(self):
        canal, cliente = self._canal(falha(429, "Too Many Requests", retry_after=7), ok())

        self.assertTrue(canal.enviar_texto(99, "oi").entregue)
        self.dorme.target.sleep.assert_called_with(7)

    def test_5xx_e_repetido(self):
        canal, cliente = self._canal(falha(502, "Bad Gateway"), ok())
        self.assertTrue(canal.enviar_texto(99, "oi").entregue)
        self.assertEqual(cliente.post.call_count, 2)

    def test_403_nao_e_repetido(self):
        # Bloqueou o bot: insistir não muda nada e só atrasa a conclusão.
        canal, cliente = self._canal(falha(403, "bot was blocked by the user"))
        resultado = canal.enviar_texto(99, "oi")

        self.assertTrue(resultado.bloqueado)
        self.assertFalse(resultado.entregue)
        self.assertEqual(cliente.post.call_count, 1)

    def test_400_nao_e_repetido(self):
        canal, cliente = self._canal(falha(400, "chat not found"))
        resultado = canal.enviar_texto(99, "oi")

        self.assertFalse(resultado.entregue)
        self.assertFalse(resultado.bloqueado)
        self.assertEqual(cliente.post.call_count, 1)


@override_settings(**SETTINGS)
class EnvioTest(TestCase):
    def _canal(self, *respostas):
        cliente = mock.Mock(spec=httpx.Client)
        cliente.post.side_effect = list(respostas)
        return TelegramCanal(cliente=cliente), cliente

    def test_id_externo_combina_conversa_e_mensagem(self):
        canal, _ = self._canal(ok(message_id=5, chat_id=99))
        self.assertEqual(canal.enviar_texto(99, "oi").id_externo, "99:5")

    def test_texto_e_cortado_no_teto_da_plataforma(self):
        canal, cliente = self._canal(ok())
        canal.enviar_texto(99, "a" * 9000)
        self.assertEqual(len(cliente.post.call_args.kwargs["json"]["text"]), 4096)

    def test_nao_manda_parse_mode(self):
        # O texto vem do agente: um `_` solto faria o MarkdownV2 recusar a
        # mensagem inteira com 400 e a pessoa ficaria sem resposta.
        canal, cliente = self._canal(ok())
        canal.enviar_texto(99, "custou 30_000 no total. veja _isto_")
        self.assertNotIn("parse_mode", cliente.post.call_args.kwargs["json"])

    @override_settings(TELEGRAM_ENABLED=False)
    def test_desligado_nao_toca_na_rede(self):
        canal, cliente = self._canal(ok())
        resultado = canal.enviar_texto(99, "oi")

        self.assertFalse(resultado.entregue)
        cliente.post.assert_not_called()


class IdentificadorTest(TestCase):
    def test_sem_chat_ou_mensagem_devolve_vazio(self):
        self.assertEqual(identificador({}), "")
        self.assertEqual(identificador({"message_id": 1}), "")
        self.assertEqual(identificador({"chat": {"id": 1}}), "")

    def test_completo(self):
        self.assertEqual(identificador({"chat": {"id": -5}, "message_id": 8}), "-5:8")


class TelegramErroTest(TestCase):
    def test_classificacao(self):
        self.assertTrue(TelegramErro(403, "x").bloqueado)
        self.assertFalse(TelegramErro(403, "x").transitorio)
        self.assertTrue(TelegramErro(429, "x").transitorio)
        self.assertTrue(TelegramErro(500, "x").transitorio)
        self.assertTrue(TelegramErro(503, "x").transitorio)
        self.assertFalse(TelegramErro(400, "x").transitorio)
