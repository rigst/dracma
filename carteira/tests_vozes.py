"""As pitadas de mitologia nos avisos automáticos.

O que importa aqui não é qual frase sai, é que ela seja estável entre
processos e que não se repita em períodos seguidos: os dois jeitos de a
personalidade virar defeito.
"""

from __future__ import annotations

import itertools

from django.test import SimpleTestCase

from carteira import vozes


class EscolhaTest(SimpleTestCase):
    def test_mesma_chave_e_periodo_dao_sempre_a_mesma_frase(self):
        """Estável entre processos: `hash()` de string é salgado por processo,
        então cada worker do Celery mandaria uma frase diferente para o mesmo
        alerta, e o teste passaria aqui e falharia no CI."""
        primeira = vozes.escolher(vozes.ESTOUROU, "limite:7:2", 9)
        for _ in range(50):
            self.assertEqual(vozes.escolher(vozes.ESTOUROU, "limite:7:2", 9), primeira)

    def test_periodos_seguidos_nunca_repetem(self):
        # Ouvir "Ícaro" três meses seguidos por azar do sorteio é exatamente o
        # que o `passo` existe para evitar.
        for chave in ("limite:1:1", "limite:2:1", "semanal:9"):
            frases = [vozes.escolher(vozes.ESTOUROU, chave, mes) for mes in range(1, 13)]
            for anterior, seguinte in itertools.pairwise(frases):
                self.assertNotEqual(anterior, seguinte, chave)

    def test_percorre_todas_as_frases(self):
        saidas = {vozes.escolher(vozes.PERTO, "limite:1:1", p) for p in range(len(vozes.PERTO))}
        self.assertEqual(saidas, set(vozes.PERTO))

    def test_chaves_diferentes_comecam_em_pontos_diferentes(self):
        inicios = {vozes.escolher(vozes.ESTOUROU, f"limite:{i}:1", 0) for i in range(40)}
        self.assertGreater(len(inicios), 1)


class ConteudoTest(SimpleTestCase):
    def test_todo_repertorio_tem_frase(self):
        for nome in ("ESTOUROU", "PERTO", "MES_NO_AZUL", "MES_NO_VERMELHO"):
            with self.subTest(repertorio=nome):
                self.assertTrue(getattr(vozes, nome))

    def test_frases_sao_curtas(self):
        # Vão emendadas num aviso que já tem números; frase longa vira parede.
        for nome in ("ESTOUROU", "PERTO", "MES_NO_AZUL", "MES_NO_VERMELHO"):
            for frase in getattr(vozes, nome):
                with self.subTest(frase=frase):
                    self.assertLessEqual(len(frase), 90)

    def test_frases_de_ma_noticia_nao_sao_comemorativas(self):
        # Quem estourou o orçamento não quer emoji de festa.
        for frase in vozes.ESTOUROU + vozes.MES_NO_VERMELHO:
            with self.subTest(frase=frase):
                self.assertNotIn("🎉", frase)
                self.assertNotIn("Parabéns", frase)
