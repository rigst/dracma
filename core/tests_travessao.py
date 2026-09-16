"""Nenhum travessão no que a pessoa lê.

O travessão (—) e a meia-risca (–) são a pontuação que mais denuncia texto
escrito por máquina. A regra existe para o produto não ter esse cheiro, e vale
para tudo que um humano lê: mensagem da assistente, template, README e também
comentário de código, que é texto como qualquer outro.

O teste é o que faz a regra durar. Sem ele, o próximo trecho escrito por uma
IA traz o travessão de volta e ninguém repara até alguém estranhar o tom.

Como trocar, quando o revisor esbarrar aqui:
  dois-pontos  quando o que vem depois explica  ("sem conta: ela não existe")
  vírgula      quando é um aparte curto         ("registrei, sem conta")
  ponto final  quando são duas ideias           ("Registrei. A conta não existe")
  parênteses   quando é mesmo um aparte longo
  ·            quando é separador visual, não pontuação de frase
"""

from __future__ import annotations

import pathlib

from django.test import SimpleTestCase

RAIZ = pathlib.Path(__file__).resolve().parent.parent

PROIBIDOS = {"—": "travessão", "–": "meia-risca"}

EXTENSOES = {".py", ".html", ".txt", ".md", ".yml"}

PASTAS_IGNORADAS = {"venv", "staticfiles", "node_modules", "__pycache__", ".git", "media", "logs"}

# A regra no prompt precisa citar o caractere para o modelo saber qual evitar.
ARQUIVOS_ISENTOS = {"ai/prompts.py", "core/tests_travessao.py"}

# Documentos legais são versionados e já publicados: mexer no texto de uma
# versão aceita é justamente o que o deploy recusa. A troca aqui só entra numa
# versão nova, e não vale forçar re-aceite por pontuação.
PASTAS_ISENTAS = ("legal/documentos/",)


def _arquivos():
    for caminho in RAIZ.rglob("*"):
        if not caminho.is_file() or caminho.suffix not in EXTENSOES:
            continue
        rel = caminho.relative_to(RAIZ)
        if PASTAS_IGNORADAS & set(rel.parts):
            continue
        if str(rel) in ARQUIVOS_ISENTOS or str(rel).startswith(PASTAS_ISENTAS):
            continue
        yield rel, caminho


class TravessaoTest(SimpleTestCase):
    def test_nenhum_arquivo_usa_travessao(self):
        achados = []
        for rel, caminho in _arquivos():
            try:
                texto = caminho.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for numero, linha in enumerate(texto.split("\n"), start=1):
                for caractere, nome in PROIBIDOS.items():
                    if caractere in linha:
                        achados.append(f"{rel}:{numero} ({nome}) {linha.strip()[:70]}")

        self.assertEqual(
            achados,
            [],
            "Travessão encontrado. Veja o docstring deste arquivo para a troca certa:\n"
            + "\n".join(achados[:20]),
        )

    def test_o_teste_esta_mesmo_olhando_os_arquivos(self):
        """Uma varredura que não acha nada passaria feliz mesmo quebrada."""
        vistos = {str(rel) for rel, _ in _arquivos()}
        self.assertIn("README.md", vistos)
        self.assertIn("ai/tools.py", vistos)
        self.assertGreater(len(vistos), 50)

    def test_a_regra_esta_no_prompt_do_agente(self):
        """O teste cobre o repositório; o prompt cobre o que a assistente
        escreve na hora, que nenhuma varredura de arquivo alcança."""
        prompt = (RAIZ / "ai" / "prompts.py").read_text(encoding="utf-8")
        self.assertIn("NUNCA use travessão", prompt)
