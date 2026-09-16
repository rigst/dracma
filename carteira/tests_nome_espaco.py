"""Renomear o espaço pela tela de Compartilhar.

Todo espaço nasce como "Meu espaço", e o nome só passa a importar quando ele
deixa de ser de uma pessoa só. Sem esta tela, um espaço de casal se chamaria
"Meu espaço" para sempre, ou exigiria alguém entrando no admin.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from accounts.models import Espaco, Usuario
from carteira.seeds import semear_categorias


class BaseNomeTest(TestCase):
    def setUp(self):
        self.espaco = Espaco.objects.create(nome="Meu espaço")
        semear_categorias(self.espaco)
        self.usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="segredo", espaco=self.espaco
        )
        self.client.force_login(self.usuario)
        self.url = reverse("carteira:renomear_espaco")

    def _renomear(self, nome):
        return self.client.post(self.url, {"nome": nome})


class RenomearTest(BaseNomeTest):
    def test_grava_o_nome_novo(self):
        self._renomear("Casa")
        self.espaco.refresh_from_db()
        self.assertEqual(self.espaco.nome, "Casa")

    def test_a_tela_volta_com_o_nome_novo(self):
        # Não fecha o diálogo: o nome não aparece no painel, então fechar não
        # mostraria nada mudando.
        resposta = self._renomear("Casa")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Casa")

    def test_a_tela_de_compartilhar_traz_o_campo(self):
        resposta = self.client.get(reverse("carteira:compartilhar"))
        self.assertContains(resposta, "Nome do espaço")
        self.assertContains(resposta, "Meu espaço")

    def test_espaco_em_branco_e_recusado(self):
        resposta = self._renomear("   ")
        self.espaco.refresh_from_db()
        self.assertEqual(self.espaco.nome, "Meu espaço")
        self.assertContains(resposta, "erro")

    def test_nome_longo_demais_e_recusado(self):
        resposta = self._renomear("x" * 200)
        self.espaco.refresh_from_db()
        self.assertEqual(self.espaco.nome, "Meu espaço")
        self.assertContains(resposta, "erro")

    def test_get_nao_renomeia(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_exige_login(self):
        self.client.logout()
        resposta = self._renomear("Casa")
        self.assertEqual(resposta.status_code, 302)
        self.espaco.refresh_from_db()
        self.assertEqual(self.espaco.nome, "Meu espaço")


class IsolamentoTest(BaseNomeTest):
    def test_so_renomeia_o_proprio_espaco(self):
        """O espaço vem do usuário logado, nunca do formulário: aceitar um id
        ali deixaria qualquer pessoa renomear o espaço de outra."""
        alheio = Espaco.objects.create(nome="Outro")
        Usuario.objects.create_user(
            username="bia", email="bia@exemplo.com", password="x", espaco=alheio
        )

        self.client.post(self.url, {"nome": "Invadido", "espaco": alheio.pk})

        alheio.refresh_from_db()
        self.espaco.refresh_from_db()
        self.assertEqual(alheio.nome, "Outro")
        self.assertEqual(self.espaco.nome, "Invadido")
