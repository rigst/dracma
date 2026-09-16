"""Webhook do Telegram: autenticidade, parsing e idempotência."""

from __future__ import annotations

import json
import time
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from bot.models import ContaTelegram, Mensagem
from bot.webhook import comando_start, extrair_mensagens, token_valido

SEGREDO = "segredo-de-teste"
CHAT = 987654321


def update_texto(message_id=41, chat_id=CHAT, texto="Uber 34 reais"):
    return {
        "update_id": 100,
        "message": {
            "message_id": message_id,
            "from": {"id": chat_id, "is_bot": False, "first_name": "Rod", "username": "rod"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1789000000,
            "text": texto,
        },
    }


@override_settings(TELEGRAM_WEBHOOK_SECRET=SEGREDO)
class SegredoTest(TestCase):
    def test_segredo_correto(self):
        self.assertTrue(token_valido(SEGREDO))

    def test_segredo_diferente(self):
        self.assertFalse(token_valido("outro"))

    def test_cabecalho_ausente(self):
        self.assertFalse(token_valido(None))
        self.assertFalse(token_valido(""))

    def test_prefixo_correto_nao_basta(self):
        # `compare_digest` e não `startswith`: um segredo que apenas começa
        # igual tem de ser recusado.
        self.assertFalse(token_valido(SEGREDO[:-1]))
        self.assertFalse(token_valido(SEGREDO + "a"))


@override_settings(TELEGRAM_WEBHOOK_SECRET="")
class SemSegredoConfiguradoTest(TestCase):
    def test_recusa_tudo(self):
        # Sem segredo configurado, aceitar qualquer POST deixaria a URL aberta
        # para quem a descobrisse.
        self.assertFalse(token_valido(""))
        self.assertFalse(token_valido("qualquer-coisa"))


class ExtrairMensagensTest(TestCase):
    def test_mensagem_de_texto(self):
        itens = extrair_mensagens(update_texto())
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["texto"], "Uber 34 reais")
        self.assertEqual(itens[0]["tipo"], "texto")
        self.assertEqual(itens[0]["chat_id"], CHAT)
        self.assertEqual(itens[0]["username"], "rod")

    def test_identificador_combina_conversa_e_mensagem(self):
        # O message_id só é único DENTRO de uma conversa: sozinho, o de um
        # usuário colidiria com o de outro e a segunda mensagem seria
        # descartada como repetição.
        um = extrair_mensagens(update_texto(message_id=7, chat_id=111))[0]
        outro = extrair_mensagens(update_texto(message_id=7, chat_id=222))[0]
        self.assertNotEqual(um["id_externo"], outro["id_externo"])
        self.assertEqual(um["id_externo"], "111:7")

    def test_audio_traz_file_id_e_duracao(self):
        update = update_texto()
        del update["message"]["text"]
        update["message"]["voice"] = {
            "file_id": "AwACAgEAAx",
            "duration": 7,
            "mime_type": "audio/ogg",
        }
        item = extrair_mensagens(update)[0]
        self.assertEqual(item["tipo"], "audio")
        self.assertEqual(item["file_id"], "AwACAgEAAx")
        self.assertEqual(item["duracao_s"], 7)
        self.assertEqual(item["mime_type"], "audio/ogg")

    def test_foto_usa_a_maior_resolucao(self):
        # A miniatura fica ilegível e o modelo erra o valor do comprovante.
        update = update_texto()
        del update["message"]["text"]
        update["message"]["photo"] = [
            {"file_id": "pequena", "width": 90, "file_size": 1200},
            {"file_id": "media", "width": 320, "file_size": 15000},
            {"file_id": "grande", "width": 1280, "file_size": 180000},
        ]
        item = extrair_mensagens(update)[0]
        self.assertEqual(item["tipo"], "imagem")
        self.assertEqual(item["file_id"], "grande")

    def test_legenda_da_imagem_vira_texto(self):
        update = update_texto()
        del update["message"]["text"]
        update["message"]["photo"] = [{"file_id": "grande"}]
        update["message"]["caption"] = "mercado do mês"
        item = extrair_mensagens(update)[0]
        self.assertEqual(item["texto"], "mercado do mês")

    def test_documento_traz_o_mime_declarado(self):
        update = update_texto()
        del update["message"]["text"]
        update["message"]["document"] = {
            "file_id": "doc1",
            "mime_type": "application/pdf",
            "file_name": "extrato.pdf",
        }
        item = extrair_mensagens(update)[0]
        self.assertEqual(item["tipo"], "documento")
        self.assertEqual(item["mime_type"], "application/pdf")

    def test_tipo_nao_suportado_e_ignorado(self):
        update = update_texto()
        del update["message"]["text"]
        update["message"]["sticker"] = {"file_id": "s1"}
        self.assertEqual(extrair_mensagens(update), [])

    def test_mensagem_de_grupo_e_ignorada(self):
        # Um bot de finanças num grupo exporia o extrato de alguém para o grupo
        # inteiro, e o pareamento não faz sentido com vários remetentes.
        update = update_texto()
        update["message"]["chat"]["type"] = "group"
        self.assertEqual(extrair_mensagens(update), [])

    def test_edicao_nao_e_reprocessada(self):
        # Reprocessar uma edição criaria um segundo lançamento para a mesma fala.
        update = update_texto()
        update["edited_message"] = update.pop("message")
        self.assertEqual(extrair_mensagens(update), [])

    def test_payload_vazio_nao_estoura(self):
        self.assertEqual(extrair_mensagens({}), [])
        self.assertEqual(extrair_mensagens({"update_id": 1}), [])


class ComandoStartTest(TestCase):
    def test_start_com_payload(self):
        self.assertEqual(comando_start("/start abc-123"), "abc-123")

    def test_start_pelado(self):
        # "" e não None: quem abriu o bot pela busca merece o convite ao
        # pareamento, não ser mandado ao agente.
        self.assertEqual(comando_start("/start"), "")

    def test_texto_comum_nao_e_start(self):
        self.assertIsNone(comando_start("almocei 32 reais"))
        self.assertIsNone(comando_start("/startup custou 50"))
        self.assertIsNone(comando_start(""))


@override_settings(TELEGRAM_ENABLED=True, TELEGRAM_WEBHOOK_SECRET=SEGREDO)
class WebhookViewTest(TestCase):
    def setUp(self):
        self.cliente = Client()
        self.url = reverse("bot:webhook")
        self.patcher = mock.patch("bot.tasks.processar_mensagem.delay")
        self.task = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _postar(self, payload, segredo=SEGREDO):
        return self.cliente.post(
            self.url,
            data=json.dumps(payload).encode(),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=segredo,
        )

    def test_segredo_invalido_da_403_e_nao_grava_nada(self):
        resposta = self._postar(update_texto(), segredo="outro")
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(Mensagem.objects.count(), 0)
        self.task.assert_not_called()

    def test_sem_cabecalho_da_403(self):
        resposta = self.cliente.post(
            self.url, data=json.dumps(update_texto()), content_type="application/json"
        )
        self.assertEqual(resposta.status_code, 403)

    def test_post_valido_grava_e_enfileira(self):
        resposta = self._postar(update_texto())
        self.assertEqual(resposta.status_code, 200)
        mensagem = Mensagem.objects.get()
        self.assertEqual(mensagem.texto, "Uber 34 reais")
        self.assertEqual(mensagem.direcao, Mensagem.Direcao.ENTRADA)
        self.assertEqual(mensagem.canal, "telegram")
        self.assertTrue(ContaTelegram.objects.filter(chat_id=CHAT).exists())
        self.task.assert_called_once_with(mensagem.pk, "", "")

    def test_conta_nova_guarda_o_username(self):
        self._postar(update_texto())
        conta = ContaTelegram.objects.get(chat_id=CHAT)
        self.assertEqual(conta.username, "rod")
        self.assertEqual(conta.primeiro_nome, "Rod")
        self.assertIsNone(conta.usuario_id)

    def test_reenvio_do_mesmo_update_nao_duplica(self):
        # O Telegram reenvia quando não vê o 200 a tempo. Sem a guarda, a mesma
        # fala do usuário viraria duas transações.
        self._postar(update_texto())
        resposta = self._postar(update_texto())
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Mensagem.objects.count(), 1)
        self.assertEqual(self.task.call_count, 1)

    def test_mesma_posicao_em_conversas_diferentes_nao_colide(self):
        self._postar(update_texto(message_id=7, chat_id=111))
        self._postar(update_texto(message_id=7, chat_id=222))
        self.assertEqual(Mensagem.objects.count(), 2)

    def test_midia_passa_file_id_e_mime_para_a_task(self):
        update = update_texto()
        del update["message"]["text"]
        update["message"]["document"] = {"file_id": "doc1", "mime_type": "application/pdf"}
        self._postar(update)
        mensagem = Mensagem.objects.get()
        self.task.assert_called_once_with(mensagem.pk, "doc1", "application/pdf")

    def test_json_invalido_devolve_200(self):
        # Reenviar não conserta corpo malformado, e insistir só faz o Telegram
        # espaçar as entregas seguintes.
        resposta = self.cliente.post(
            self.url,
            data=b"{nao e json",
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=SEGREDO,
        )
        self.assertEqual(resposta.status_code, 200)

    def test_update_sem_mensagem_devolve_200(self):
        resposta = self._postar({"update_id": 5, "callback_query": {"id": "1"}})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Mensagem.objects.count(), 0)

    def test_a_view_nao_processa_nada_de_pesado(self):
        # Respostas lentas repetidas fazem o Telegram espaçar os updates até o
        # bot ficar mudo. Medimos com folga enorme para o teste não ficar
        # instável em CI carregado, mas ainda pegar uma chamada síncrona à
        # Claude ou um download de mídia que tenham entrado na view.
        inicio = time.monotonic()
        self._postar(update_texto())
        self.assertLess(time.monotonic() - inicio, 1.0)

    def test_get_nao_e_aceito(self):
        # A Bot API só faz POST; não há handshake GET como havia na Meta.
        self.assertEqual(self.cliente.get(self.url).status_code, 405)

    @override_settings(TELEGRAM_ENABLED=False)
    def test_desligado_responde_404(self):
        self.assertEqual(self._postar(update_texto()).status_code, 404)
