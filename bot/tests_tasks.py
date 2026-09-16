"""Processamento de mensagem: pareamento, mídia, agente e resposta."""

from __future__ import annotations

import base64
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Espaco, Usuario
from ai.fakes import ClienteFalso
from carteira.models import Origem, Transacao
from carteira.seeds import semear_categorias
from bot.canais.base import MidiaBaixada
from bot.canais.fake import FakeCanal
from bot.conteudo import montar
from bot.models import CodigoPareamento, Mensagem, Midia, ContaTelegram
from bot.tasks import processar_mensagem
from bot import envio

PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class BaseTaskTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        self.conta = ContaTelegram.objects.create(
            chat_id=987654321, usuario=self.usuario, verificado_em=timezone.now()
        )
        self.canal = FakeCanal()
        self.patch_canal = mock.patch("bot.tasks.obter_canal", return_value=self.canal)
        self.patch_canal.start()
        self.addCleanup(self.patch_canal.stop)

    def _entrada(self, texto="Uber 34 reais", tipo=Mensagem.Tipo.TEXTO, id_externo="123:1"):
        return Mensagem.objects.create(
            conta=self.conta,
            usuario=self.usuario,
            canal="fake",
            direcao=Mensagem.Direcao.ENTRADA,
            tipo=tipo,
            id_externo=id_externo,
            texto=texto,
        )

    def _agente(self, cliente):
        return mock.patch("bot.tasks.responder", wraps=_agente_real(cliente))


def _agente_real(cliente):
    from ai.agente import responder as real

    def chamada(contexto, conteudo, historico=None):
        return real(contexto, conteudo, historico=historico, cliente=cliente)

    return chamada


class ProcessamentoTest(BaseTaskTest):
    def test_registra_e_responde(self):
        mensagem = self._entrada()
        cliente = (
            ClienteFalso()
            .chama(
                "registrar_transacao",
                valor=34,
                descricao="Uber",
                tipo="despesa",
                categoria="Transporte",
                conta="",
                data=timezone.localdate().isoformat(),
                pago=True,
            )
            .responde("Registrei: Uber, R$ 34,00 🚗")
        )
        with self._agente(cliente):
            processar_mensagem(mensagem.pk)

        transacao = Transacao.objects.get()
        self.assertEqual(transacao.valor, Decimal("34.00"))
        self.assertEqual(transacao.origem, Origem.TEXTO)
        # A resposta do agente é a PRIMEIRA saída; depois dela o roteiro de
        # primeiros passos manda as boas-vindas.
        self.assertEqual(self.canal.enviadas[0]["texto"], "Registrei: Uber, R$ 34,00 🚗")
        mensagem.refresh_from_db()
        self.assertEqual(mensagem.status, Mensagem.Status.RESPONDIDA)

    def test_reprocessar_a_mesma_mensagem_nao_duplica(self):
        # O broker pode reentregar a task. Sem a guarda de status, o mesmo
        # áudio viraria duas transações.
        mensagem = self._entrada()
        cliente = ClienteFalso().responde("ok")
        with self._agente(cliente):
            processar_mensagem(mensagem.pk)
            processar_mensagem(mensagem.pk)
        self.assertEqual(len(cliente.chamadas), 1)

    def test_mensagem_inexistente_nao_estoura(self):
        processar_mensagem(999999)

    def test_falha_do_agente_avisa_a_pessoa(self):
        mensagem = self._entrada()
        with mock.patch("bot.tasks.responder", side_effect=RuntimeError("boom")):
            processar_mensagem(mensagem.pk)
        mensagem.refresh_from_db()
        self.assertEqual(mensagem.status, Mensagem.Status.ERRO)
        self.assertIn("problema", self.canal.ultimo_texto)

    @override_settings(QUOTA_TOKENS_DEFAULT=0)
    def test_sem_quota_explica_em_vez_de_falhar(self):
        mensagem = self._entrada()
        with self._agente(ClienteFalso().responde("x")):
            processar_mensagem(mensagem.pk)
        self.assertIn("cota", self.canal.ultimo_texto)
        mensagem.refresh_from_db()
        self.assertEqual(mensagem.status, Mensagem.Status.RESPONDIDA)

    def test_historico_alimenta_a_conversa(self):
        Mensagem.objects.create(
            conta=self.conta,
            usuario=self.usuario,
            canal="fake",
            direcao=Mensagem.Direcao.ENTRADA,
            texto="quanto gastei?",
            id_externo="w0",
        )
        Mensagem.objects.create(
            conta=self.conta,
            usuario=self.usuario,
            canal="fake",
            direcao=Mensagem.Direcao.SAIDA,
            texto="R$ 120 até agora",
            id_externo="w0b",
        )
        mensagem = self._entrada(texto="e com mercado?")
        cliente = ClienteFalso().responde("R$ 80")
        with self._agente(cliente):
            processar_mensagem(mensagem.pk)

        papeis = [m["role"] for m in cliente.ultima_chamada["messages"]]
        self.assertEqual(papeis, ["user", "assistant", "user"])


class PareamentoTest(BaseTaskTest):
    def setUp(self):
        super().setUp()
        self.desconhecido = ContaTelegram.objects.create(chat_id=555000111)

    def _entrada_desconhecida(self, texto):
        return Mensagem.objects.create(
            conta=self.desconhecido,
            canal="fake",
            direcao=Mensagem.Direcao.ENTRADA,
            texto=texto,
            id_externo="123:novo",
        )

    def test_conversa_desconhecida_recebe_convite(self):
        processar_mensagem(self._entrada_desconhecida("oi").pk)
        self.assertIn("Conectar Telegram", self.canal.ultimo_texto)
        self.assertIn("6 dígitos", self.canal.ultimo_texto)
        self.assertEqual(Transacao.objects.count(), 0)

    def test_codigo_valido_vincula_a_conta(self):
        CodigoPareamento.objects.create(
            usuario=self.usuario,
            codigo="123456",
            expira_em=timezone.now() + timedelta(minutes=10),
        )
        processar_mensagem(self._entrada_desconhecida("123456").pk)

        self.desconhecido.refresh_from_db()
        self.assertEqual(self.desconhecido.usuario, self.usuario)
        self.assertTrue(self.desconhecido.vinculado)
        # As boas-vindas do roteiro entram no lugar de uma confirmação solta.
        self.assertIn("conectei", self.canal.ultimo_texto)
        self.assertIn("áudio", self.canal.ultimo_texto)

    def test_codigo_expirado_nao_vincula(self):
        CodigoPareamento.objects.create(
            usuario=self.usuario,
            codigo="123456",
            expira_em=timezone.now() - timedelta(minutes=1),
        )
        processar_mensagem(self._entrada_desconhecida("123456").pk)
        self.desconhecido.refresh_from_db()
        self.assertIsNone(self.desconhecido.usuario)

    def test_codigo_ja_usado_nao_vincula_de_novo(self):
        CodigoPareamento.objects.create(
            usuario=self.usuario,
            codigo="123456",
            usado_em=timezone.now(),
            expira_em=timezone.now() + timedelta(minutes=10),
        )
        processar_mensagem(self._entrada_desconhecida("123456").pk)
        self.desconhecido.refresh_from_db()
        self.assertIsNone(self.desconhecido.usuario)


class MidiaTest(BaseTaskTest):
    def test_imagem_e_baixada_e_vai_como_bloco_nativo(self):
        self.canal.midias["media-1"] = MidiaBaixada(
            conteudo=PNG_1x1, mime_type="image/png", tamanho=len(PNG_1x1)
        )
        mensagem = self._entrada(texto="mercado do mês", tipo=Mensagem.Tipo.IMAGEM)
        cliente = ClienteFalso().responde("Li o comprovante ✅")
        with self._agente(cliente):
            processar_mensagem(mensagem.pk, "media-1")

        midia = Midia.objects.get()
        self.assertEqual(midia.mime_type, "image/png")

        conteudo = cliente.ultimo_conteudo_do_usuario
        self.assertEqual(conteudo[0]["type"], "image")
        self.assertEqual(conteudo[0]["source"]["media_type"], "image/png")
        self.assertEqual(conteudo[1]["text"], "mercado do mês")

    def test_audio_e_transcrito_antes_de_ir_para_a_claude(self):
        # A API da Anthropic não aceita áudio: sem a transcrição, o agente
        # receberia uma mensagem vazia.
        self.canal.midias["media-2"] = MidiaBaixada(
            conteudo=b"ogg-falso", mime_type="audio/ogg", tamanho=9
        )
        mensagem = self._entrada(texto="", tipo=Mensagem.Tipo.AUDIO)
        cliente = ClienteFalso().responde("Registrei ✅")

        with mock.patch("ai.transcricao.transcrever", return_value="comprei 87 e 40 no mercado"):
            with self._agente(cliente):
                processar_mensagem(mensagem.pk, "media-2")

        mensagem.refresh_from_db()
        self.assertEqual(mensagem.transcricao, "comprei 87 e 40 no mercado")
        self.assertEqual(cliente.ultimo_conteudo_do_usuario, "comprei 87 e 40 no mercado")

    def test_audio_longo_demais_nao_trava_o_worker(self):
        from ai.transcricao import AudioLongoDemais

        self.canal.midias["media-3"] = MidiaBaixada(
            conteudo=b"ogg", mime_type="audio/ogg", tamanho=3
        )
        mensagem = self._entrada(texto="", tipo=Mensagem.Tipo.AUDIO)
        with mock.patch("ai.transcricao.transcrever", side_effect=AudioLongoDemais("longo")):
            with self._agente(ClienteFalso().responde("ok")):
                processar_mensagem(mensagem.pk, "media-3")
        mensagem.refresh_from_db()
        self.assertEqual(mensagem.transcricao, "")

    def test_falha_no_download_avisa_a_pessoa(self):
        mensagem = self._entrada(tipo=Mensagem.Tipo.IMAGEM)
        processar_mensagem(mensagem.pk, "inexistente")
        mensagem.refresh_from_db()
        self.assertEqual(mensagem.status, Mensagem.Status.ERRO)
        self.assertIn("arquivo", self.canal.ultimo_texto)


class ConteudoTest(TestCase):
    def setUp(self):
        self.mensagem = Mensagem.objects.create(
            canal="console", direcao=Mensagem.Direcao.ENTRADA, texto="almoço 32"
        )

    def test_so_texto_devolve_string(self):
        self.assertEqual(montar(self.mensagem), "almoço 32")

    def test_mensagem_vazia_nao_vira_string_vazia(self):
        # Conteúdo vazio é rejeitado pela API; melhor um marcador explícito.
        self.mensagem.texto = ""
        self.assertEqual(montar(self.mensagem), "(mensagem vazia)")

    def test_pdf_vira_bloco_document(self):
        midia = Midia(mensagem=self.mensagem, mime_type="application/pdf")
        midia.arquivo.save("x.pdf", ContentFile(b"%PDF-1.4 fake"), save=False)
        midia.save()
        self.mensagem.refresh_from_db()

        blocos = montar(self.mensagem)
        self.assertEqual(blocos[0]["type"], "document")
        self.assertEqual(blocos[0]["source"]["media_type"], "application/pdf")

    def test_tipo_de_midia_nao_suportado_cai_no_texto(self):
        midia = Midia(mensagem=self.mensagem, mime_type="application/zip")
        midia.arquivo.save("x.zip", ContentFile(b"PK"), save=False)
        midia.save()
        self.mensagem.refresh_from_db()
        self.assertEqual(montar(self.mensagem), "almoço 32")

    def test_imagem_sem_legenda_ganha_instrucao_padrao(self):
        self.mensagem.texto = ""
        self.mensagem.save()
        midia = Midia(mensagem=self.mensagem, mime_type="image/jpeg")
        midia.arquivo.save("x.jpg", ContentFile(PNG_1x1), save=False)
        midia.save()
        self.mensagem.refresh_from_db()

        blocos = montar(self.mensagem)
        self.assertIn("comprovante", blocos[1]["text"])


class HistoricoComToolsTest(BaseTaskTest):
    """A regressão das duas Ubers.

    O histórico entre turnos era só texto. O modelo lia a própria confirmação
    ("Uber de R$ 20,00 registrado ✅") como narração e refazia a tool no turno
    seguinte: criou uma segunda Uber, editou a duplicata e confirmou um ajuste
    que nunca tocou no lançamento original.
    """

    def _turno(self, texto_entrada, cliente, id_externo):
        from bot.tasks import processar_mensagem

        entrada = Mensagem.objects.create(
            conta=self.conta,
            usuario=self.usuario,
            canal="fake",
            direcao=Mensagem.Direcao.ENTRADA,
            tipo=Mensagem.Tipo.TEXTO,
            id_externo=id_externo,
            texto=texto_entrada,
        )
        with self._agente(cliente):
            processar_mensagem(entrada.pk)
        return entrada

    def test_a_resposta_guarda_os_blocos_de_tool(self):
        cliente = (
            ClienteFalso()
            .chama(
                "registrar_transacao",
                valor=20,
                descricao="Uber",
                tipo="despesa",
                categoria="Transporte",
            )
            .responde("Uber de R$ 20,00 registrado ✅")
        )
        self._turno("gastei 20 de uber", cliente, "123:10")

        # Pela resposta em si: `avancar` cria outra saída (a dica do roteiro)
        # logo depois, e ela não tem turno nenhum.
        saida = Mensagem.objects.get(
            direcao=Mensagem.Direcao.SAIDA, texto="Uber de R$ 20,00 registrado ✅"
        )
        self.assertIsNotNone(saida.turno)
        tipos = [
            b["type"]
            for m in saida.turno
            if isinstance(m["content"], list)
            for b in m["content"]
        ]
        self.assertIn("tool_use", tipos)
        self.assertIn("tool_result", tipos)

    def test_o_turno_seguinte_ve_que_a_escrita_aconteceu(self):
        from bot.tasks import _historico

        cliente = (
            ClienteFalso()
            .chama(
                "registrar_transacao",
                valor=20,
                descricao="Uber",
                tipo="despesa",
                categoria="Transporte",
            )
            .responde("Uber de R$ 20,00 registrado ✅")
        )
        self._turno("gastei 20 de uber", cliente, "123:10")

        seguinte = Mensagem.objects.create(
            conta=self.conta,
            usuario=self.usuario,
            canal="fake",
            direcao=Mensagem.Direcao.ENTRADA,
            tipo=Mensagem.Tipo.TEXTO,
            id_externo="123:11",
            texto="ajusta o uber para 22",
        )
        historico = _historico(seguinte)

        tipos = [
            b["type"]
            for m in historico
            if isinstance(m["content"], list)
            for b in m["content"]
        ]
        self.assertIn("tool_use", tipos)
        self.assertIn("tool_result", tipos)
        # E a fala do usuário aparece UMA vez só.
        self.assertEqual(
            sum(1 for m in historico if m["content"] == "gastei 20 de uber"), 1
        )

    def test_historico_comeca_sempre_pelo_usuario(self):
        # A janela pode cair logo depois de uma mensagem do roteiro de
        # onboarding, que não responde a ninguém — e a API exige começar no
        # usuário.
        from bot.tasks import _historico

        envio.responder(self.conta, "dica do roteiro", canal=self.canal)
        nova = Mensagem.objects.create(
            conta=self.conta,
            usuario=self.usuario,
            canal="fake",
            direcao=Mensagem.Direcao.ENTRADA,
            tipo=Mensagem.Tipo.TEXTO,
            id_externo="123:12",
            texto="e aí?",
        )
        historico = _historico(nova)
        self.assertTrue(not historico or historico[0]["role"] == "user")

    def test_mensagem_antiga_sem_turno_ainda_entra_como_texto(self):
        # Tudo que foi gravado antes deste campo existir.
        from bot.tasks import _historico

        Mensagem.objects.create(
            conta=self.conta,
            usuario=self.usuario,
            canal="fake",
            direcao=Mensagem.Direcao.ENTRADA,
            tipo=Mensagem.Tipo.TEXTO,
            id_externo="123:20",
            texto="pergunta antiga",
        )
        Mensagem.objects.create(
            conta=self.conta,
            usuario=self.usuario,
            canal="fake",
            direcao=Mensagem.Direcao.SAIDA,
            tipo=Mensagem.Tipo.TEXTO,
            texto="resposta antiga",
        )
        nova = Mensagem.objects.create(
            conta=self.conta,
            usuario=self.usuario,
            canal="fake",
            direcao=Mensagem.Direcao.ENTRADA,
            tipo=Mensagem.Tipo.TEXTO,
            id_externo="123:21",
            texto="nova",
        )
        conteudos = [m["content"] for m in _historico(nova)]
        self.assertIn("pergunta antiga", conteudos)
        self.assertIn("resposta antiga", conteudos)
