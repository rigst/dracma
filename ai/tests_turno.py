"""O transcript do turno — o que impede o agente de refazer a própria escrita.

Nasceu de um bug de produção. O histórico entre turnos era só texto, então o
modelo lia a própria confirmação ("Uber de R$ 20,00 registrado ✅") como
narração e não como prova de que a escrita tinha acontecido. No turno
seguinte ele refez a tool: criou uma segunda Uber, editou a duplicata e
confirmou um ajuste que nunca tocou no lançamento original.

O que trava isso é reproduzir os blocos `tool_use`/`tool_result`, e é o que
estes testes cobrem.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from accounts.models import Espaco, Usuario
from ai.agente import Contexto, responder
from ai.fakes import ClienteFalso
from carteira.seeds import semear_categorias


class BaseTurnoTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Casa")
        semear_categorias(self.espaco)
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=self.espaco
        )
        self.contexto = Contexto(
            espaco=self.espaco, usuario=self.usuario, hoje=date(2026, 9, 16)
        )

    def _tipos(self, turno):
        return [
            (m["role"], [b["type"] for b in m["content"]] if isinstance(m["content"], list) else "texto")
            for m in turno
        ]


class TranscriptTest(BaseTurnoTest):
    def test_registra_e_guarda_a_prova_da_escrita(self):
        cliente = (
            ClienteFalso()
            .chama("registrar_transacao", valor=20, descricao="Uber", tipo="despesa",
                   categoria="Transporte")
            .responde("Uber de R$ 20,00 registrado ✅")
        )
        r = responder(self.contexto, "gastei 20 de uber", cliente=cliente)

        # É isto que faltava: o tool_use e o tool_result no histórico.
        self.assertEqual(
            self._tipos(r.turno),
            [("assistant", ["tool_use"]), ("user", ["tool_result"]), ("assistant", ["text"])],
        )

    def test_a_fala_do_usuario_fica_de_fora(self):
        """Ela já é a Mensagem de entrada no banco. Guardá-la aqui também
        obrigaria quem monta o histórico a descobrir qual entrada já está
        coberta por qual turno — e errar isso perde uma fala."""
        cliente = ClienteFalso().responde("beleza")
        r = responder(self.contexto, "oi", cliente=cliente)

        self.assertEqual(self._tipos(r.turno), [("assistant", ["text"])])
        self.assertNotIn("oi", str(r.turno))

    def test_o_resultado_da_tool_vai_junto(self):
        cliente = (
            ClienteFalso()
            .chama("registrar_transacao", valor=20, descricao="Uber", tipo="despesa",
                   categoria="Transporte")
            .responde("ok")
        )
        r = responder(self.contexto, "gastei 20 de uber", cliente=cliente)

        resultado = r.turno[1]["content"][0]
        self.assertEqual(resultado["type"], "tool_result")
        self.assertIn("Registrado", resultado["content"])
        # O código volta no resultado: é assim que o próximo turno sabe qual
        # lançamento editar sem criar outro.
        self.assertIn("código=", resultado["content"])

    def test_turno_e_json_puro(self):
        """Vai para um JSONField: um objeto do SDK aqui estouraria na gravação."""
        import json

        cliente = (
            ClienteFalso()
            .chama("registrar_transacao", valor=20, descricao="Uber", tipo="despesa",
                   categoria="Transporte")
            .responde("ok")
        )
        r = responder(self.contexto, "gastei 20 de uber", cliente=cliente)
        self.assertEqual(json.loads(json.dumps(r.turno)), r.turno)


class ReplayTest(BaseTurnoTest):
    def test_o_turno_devolvido_serve_de_historico_no_seguinte(self):
        # O cenário exato do bug: registra, e no turno seguinte a pessoa pede
        # um ajuste. O modelo tem de ver que a escrita já aconteceu.
        cliente = (
            ClienteFalso()
            .chama("registrar_transacao", valor=20, descricao="Uber", tipo="despesa",
                   categoria="Transporte")
            .responde("Uber de R$ 20,00 registrado ✅")
        )
        primeiro = responder(self.contexto, "gastei 20 de uber", cliente=cliente)

        historico = [{"role": "user", "content": "gastei 20 de uber"}, *primeiro.turno]
        segundo = ClienteFalso().responde("ajustado")
        responder(self.contexto, "ajusta o uber para 22", historico=historico, cliente=segundo)

        enviadas = segundo.ultima_chamada["messages"]
        blocos = [b["type"] for m in enviadas if isinstance(m["content"], list) for b in m["content"]]
        self.assertIn("tool_use", blocos)
        self.assertIn("tool_result", blocos)


class HigieneTest(BaseTurnoTest):
    def test_midia_nao_volta_no_historico(self):
        """Remandar a imagem de todo turno anterior multiplicaria o custo por
        nada — o que importava dela já virou lançamento."""
        cliente = ClienteFalso().responde("li o comprovante")
        conteudo = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": "A" * 5000}},
            {"type": "text", "text": "quanto foi?"},
        ]
        r = responder(self.contexto, conteudo, cliente=cliente)
        self.assertNotIn("AAAA", str(r.turno))

    def test_tool_use_sem_resultado_nao_entra(self):
        """O teto de iterações corta o laço no meio. Um `tool_use` sem o
        `tool_result` correspondente faz a API recusar o histórico com 400."""
        cliente = ClienteFalso()
        for i in range(8):
            cliente.chama("consultar_limites")
        r = responder(self.contexto, "e aí?", cliente=cliente)

        ultima = r.turno[-1] if r.turno else None
        if ultima is not None and isinstance(ultima["content"], list):
            tipos = [b["type"] for b in ultima["content"]]
            self.assertNotIn("tool_use", tipos)

    def test_historico_truncado_nao_comeca_por_tool_result(self):
        # Garante que o que sai daqui é montável: a API exige que a conversa
        # comece por uma fala do usuário.
        cliente = (
            ClienteFalso()
            .chama("consultar_limites")
            .responde("nenhum limite")
        )
        r = responder(self.contexto, "meus limites?", cliente=cliente)
        self.assertEqual(r.turno[0]["role"], "assistant")
