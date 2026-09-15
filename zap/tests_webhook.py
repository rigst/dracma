"""Webhook da Meta: assinatura, handshake, parsing e idempotência."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from zap.models import Mensagem, NumeroWhatsApp
from zap.webhook import assinatura_valida, extrair_mensagens, verificar_handshake

SEGREDO = "segredo-de-teste"


def assinar(corpo: bytes, segredo: str = SEGREDO) -> str:
    return "sha256=" + hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()


def payload_texto(wamid="wamid.AAA", de="5511999998888", texto="Uber 34 reais"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "id": wamid,
                                    "from": de,
                                    "timestamp": "1789000000",
                                    "type": "text",
                                    "text": {"body": texto},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


@override_settings(WHATSAPP_APP_SECRET=SEGREDO)
class AssinaturaTest(TestCase):
    def test_assinatura_correta(self):
        corpo = b'{"a":1}'
        self.assertTrue(assinatura_valida(corpo, assinar(corpo)))

    def test_assinatura_de_outro_segredo(self):
        corpo = b'{"a":1}'
        self.assertFalse(assinatura_valida(corpo, assinar(corpo, "outro")))

    def test_corpo_alterado_invalida(self):
        # É o ponto de todo o mecanismo: assinar um corpo e mandar outro.
        self.assertFalse(assinatura_valida(b'{"a":2}', assinar(b'{"a":1}')))

    def test_cabecalho_ausente_ou_malformado(self):
        for cabecalho in (None, "", "abc", "sha1=xyz"):
            with self.subTest(cabecalho=cabecalho):
                self.assertFalse(assinatura_valida(b"{}", cabecalho))

    @override_settings(WHATSAPP_APP_SECRET="")
    def test_sem_segredo_configurado_recusa_tudo(self):
        # Fail-closed: sem segredo, aceitar seria deixar qualquer um que
        # descubra a URL injetar transação na conta alheia.
        corpo = b"{}"
        self.assertFalse(assinatura_valida(corpo, assinar(corpo, "")))


@override_settings(WHATSAPP_VERIFY_TOKEN="tok-123")
class HandshakeTest(TestCase):
    def test_token_correto_devolve_o_desafio(self):
        desafio = verificar_handshake(
            {"hub.mode": "subscribe", "hub.verify_token": "tok-123", "hub.challenge": "9876"}
        )
        self.assertEqual(desafio, "9876")

    def test_token_errado(self):
        self.assertIsNone(
            verificar_handshake(
                {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "9876"}
            )
        )

    def test_modo_errado(self):
        self.assertIsNone(
            verificar_handshake(
                {"hub.mode": "unsubscribe", "hub.verify_token": "tok-123", "hub.challenge": "1"}
            )
        )


class ExtrairMensagensTest(TestCase):
    def test_mensagem_de_texto(self):
        itens = extrair_mensagens(payload_texto())
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["texto"], "Uber 34 reais")
        self.assertEqual(itens[0]["de"], "5511999998888")

    def test_varias_mensagens_no_mesmo_post(self):
        # A Meta agrupa: tratar só a primeira perde lançamento quando a pessoa
        # manda três áudios seguidos.
        payload = payload_texto()
        payload["entry"][0]["changes"][0]["value"]["messages"].append(
            {
                "id": "wamid.BBB",
                "from": "5511999998888",
                "type": "text",
                "text": {"body": "Almoço 32"},
            }
        )
        self.assertEqual(len(extrair_mensagens(payload)), 2)

    def test_audio_traz_media_id_e_duracao(self):
        payload = payload_texto()
        payload["entry"][0]["changes"][0]["value"]["messages"] = [
            {
                "id": "wamid.CCC",
                "from": "5511999998888",
                "type": "audio",
                "audio": {"id": "media-1", "mime_type": "audio/ogg; codecs=opus", "seconds": 7},
            }
        ]
        item = extrair_mensagens(payload)[0]
        self.assertEqual(item["tipo"], "audio")
        self.assertEqual(item["media_id"], "media-1")
        self.assertEqual(item["duracao_s"], 7)

    def test_legenda_da_imagem_vira_texto(self):
        # "mercado do mês" junto da foto do comprovante é contexto útil.
        payload = payload_texto()
        payload["entry"][0]["changes"][0]["value"]["messages"] = [
            {
                "id": "wamid.DDD",
                "from": "5511999998888",
                "type": "image",
                "image": {"id": "media-2", "mime_type": "image/jpeg", "caption": "mercado do mês"},
            }
        ]
        item = extrair_mensagens(payload)[0]
        self.assertEqual(item["tipo"], "imagem")
        self.assertEqual(item["texto"], "mercado do mês")

    def test_tipo_nao_suportado_e_ignorado(self):
        payload = payload_texto()
        payload["entry"][0]["changes"][0]["value"]["messages"] = [
            {"id": "w", "from": "5511", "type": "sticker", "sticker": {"id": "x"}}
        ]
        self.assertEqual(extrair_mensagens(payload), [])

    def test_evento_de_status_nao_e_mensagem(self):
        payload = {
            "entry": [
                {"changes": [{"value": {"statuses": [{"id": "wamid.X", "status": "delivered"}]}}]}
            ]
        }
        self.assertEqual(extrair_mensagens(payload), [])

    def test_payload_vazio_nao_estoura(self):
        for payload in ({}, {"entry": []}, {"entry": [{"changes": []}]}):
            with self.subTest(payload=payload):
                self.assertEqual(extrair_mensagens(payload), [])


@override_settings(
    WHATSAPP_ENABLED=True, WHATSAPP_APP_SECRET=SEGREDO, WHATSAPP_VERIFY_TOKEN="tok-123"
)
class WebhookViewTest(TestCase):
    def setUp(self):
        self.cliente = Client()
        self.url = reverse("zap:webhook")
        self.patcher = mock.patch("zap.tasks.processar_mensagem.delay")
        self.task = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _postar(self, payload, segredo=SEGREDO):
        corpo = json.dumps(payload).encode()
        return self.cliente.post(
            self.url,
            data=corpo,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=assinar(corpo, segredo),
        )

    def test_get_handshake(self):
        resposta = self.cliente.get(
            self.url,
            {"hub.mode": "subscribe", "hub.verify_token": "tok-123", "hub.challenge": "9876"},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.content, b"9876")

    def test_get_com_token_errado_da_403(self):
        resposta = self.cliente.get(
            self.url,
            {"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "1"},
        )
        self.assertEqual(resposta.status_code, 403)

    def test_assinatura_invalida_da_403_e_nao_grava_nada(self):
        resposta = self._postar(payload_texto(), segredo="outro")
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(Mensagem.objects.count(), 0)
        self.task.assert_not_called()

    def test_post_valido_grava_e_enfileira(self):
        resposta = self._postar(payload_texto())
        self.assertEqual(resposta.status_code, 200)
        mensagem = Mensagem.objects.get()
        self.assertEqual(mensagem.texto, "Uber 34 reais")
        self.assertEqual(mensagem.direcao, Mensagem.Direcao.ENTRADA)
        self.assertTrue(NumeroWhatsApp.objects.filter(numero="5511999998888").exists())
        self.task.assert_called_once_with(mensagem.pk, "")

    def test_reenvio_do_mesmo_wamid_nao_duplica(self):
        # A Meta reenvia por conta própria. Sem a guarda, a mesma fala do
        # usuário viraria duas transações.
        self._postar(payload_texto())
        resposta = self._postar(payload_texto())
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Mensagem.objects.count(), 1)
        self.assertEqual(self.task.call_count, 1)

    def test_json_invalido_devolve_200(self):
        # Reenviar não conserta corpo malformado, e insistir aproxima a Meta de
        # desabilitar a subscrição.
        corpo = b"{nao e json"
        resposta = self.cliente.post(
            self.url,
            data=corpo,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=assinar(corpo),
        )
        self.assertEqual(resposta.status_code, 200)

    def test_a_view_nao_processa_nada_de_pesado(self):
        # A Meta desabilita a subscrição depois de respostas lentas repetidas.
        # O orçamento real é ~5s; medimos com folga enorme para o teste não
        # ficar instável em CI carregado, mas ainda pegar uma chamada síncrona
        # à Claude ou um download de mídia que tenham entrado na view.
        inicio = time.monotonic()
        self._postar(payload_texto())
        self.assertLess(time.monotonic() - inicio, 1.0)

    @override_settings(WHATSAPP_ENABLED=False)
    def test_desligado_responde_404(self):
        self.assertEqual(self.cliente.get(self.url).status_code, 404)

    def test_status_failed_marca_a_saida_como_erro(self):
        numero = NumeroWhatsApp.objects.create(numero="5551999998888")
        enviada = Mensagem.objects.create(
            numero=numero,
            canal="cloud_api",
            direcao=Mensagem.Direcao.SAIDA,
            wamid="wamid.SAIU",
            texto="oi",
            status=Mensagem.Status.RESPONDIDA,
        )

        resposta = self._postar(
            {
                "entry": [
                    {
                        "changes": [
                            {
                                "value": {
                                    "statuses": [
                                        {
                                            "id": "wamid.SAIU",
                                            "status": "failed",
                                            "errors": [{"code": 131030, "title": "fora da lista"}],
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        )

        self.assertEqual(resposta.status_code, 200)
        enviada.refresh_from_db()
        self.assertEqual(enviada.status, Mensagem.Status.ERRO)
        self.assertIn("131030", enviada.erro)

    def test_status_delivered_nao_mexe_na_mensagem(self):
        numero = NumeroWhatsApp.objects.create(numero="5551999998888")
        enviada = Mensagem.objects.create(
            numero=numero,
            canal="cloud_api",
            direcao=Mensagem.Direcao.SAIDA,
            wamid="wamid.OK",
            texto="oi",
            status=Mensagem.Status.RESPONDIDA,
        )

        self._postar(
            {
                "entry": [
                    {
                        "changes": [
                            {"value": {"statuses": [{"id": "wamid.OK", "status": "delivered"}]}}
                        ]
                    }
                ]
            }
        )

        enviada.refresh_from_db()
        self.assertEqual(enviada.status, Mensagem.Status.RESPONDIDA)
