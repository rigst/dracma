"""A config do nginx, nos pontos onde um descuido quebra algo em silêncio.

O arquivo em `deploy/nginx/` é a fonte; o que está em `/etc/nginx` é cópia.
Estes testes olham a fonte, então pegam o erro antes de ele ir para o servidor.
"""

from __future__ import annotations

import pathlib
import re

from django.test import SimpleTestCase

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "nginx" / "dracma"

CABECALHOS = (
    "Content-Security-Policy",
    "Referrer-Policy",
    "Permissions-Policy",
    "X-Content-Type-Options",
)


def _bloco(texto: str, abertura: str) -> str:
    """O conteúdo de um bloco do nginx, por contagem de chaves."""
    inicio = texto.index(abertura) + len(abertura)
    profundidade = 1
    for posicao in range(inicio, len(texto)):
        if texto[posicao] == "{":
            profundidade += 1
        elif texto[posicao] == "}":
            profundidade -= 1
            if profundidade == 0:
                return texto[inicio:posicao]
    raise AssertionError(f"bloco {abertura!r} não fecha")


def _csp(bloco: str) -> str:
    achado = re.search(r'add_header Content-Security-Policy "([^"]+)"', bloco)
    assert achado, "sem Content-Security-Policy no bloco"
    return achado.group(1)


class ReferrerPolicyTest(SimpleTestCase):
    """Um cabeçalho só, e o mesmo valor em todo lugar.

    O Django manda o `Referrer-Policy` dele em toda resposta do app e o nginx
    mandava o seu, com valor diferente: o navegador recebia dois. O nginx é a
    fonte única porque é o único que também cobre /static/ e /media/, que não
    passam pelo app.
    """

    def setUp(self):
        self.texto = CONFIG.read_text(encoding="utf-8")

    def test_um_valor_so_no_arquivo_inteiro(self):
        valores = set(re.findall(r'add_header Referrer-Policy "([^"]+)"', self.texto))
        self.assertEqual(valores, {"same-origin"})

    def test_bate_com_o_do_django(self):
        from django.conf import settings as cfg

        producao = pathlib.Path(cfg.BASE_DIR) / "config" / "settings" / "production.py"
        declarado = re.search(
            r'SECURE_REFERRER_POLICY = "([^"]+)"', producao.read_text(encoding="utf-8")
        )
        self.assertIsNotNone(declarado, "production.py não declara SECURE_REFERRER_POLICY")
        self.assertEqual(declarado.group(1), "same-origin")

    def test_toda_rota_que_faz_proxy_descarta_o_do_app(self):
        """Sem `proxy_hide_header`, o do Django volta a somar com o do nginx."""
        for abertura in ("location / {", "location /admin/ {"):
            with self.subTest(location=abertura):
                self.assertIn("proxy_hide_header Referrer-Policy;", _bloco(self.texto, abertura))


class CspDoAdminTest(SimpleTestCase):
    """O admin precisa de 'unsafe-eval'; o portal não pode ter.

    O Unfold roda sobre Alpine.js, e o build distribuído monta funções em
    tempo de execução. Sem 'unsafe-eval' o navegador barra toda expressão do
    Alpine: o painel "Available shortcuts" abre e não fecha mais, cobrindo a
    tela, e menus, tema, filtros e sidebar param junto.
    """

    def setUp(self):
        self.texto = CONFIG.read_text(encoding="utf-8")
        self.admin = _bloco(self.texto, "location /admin/ {")

    def test_o_admin_permite_unsafe_eval(self):
        self.assertIn("'unsafe-eval'", _csp(self.admin))

    def test_o_resto_do_site_nao_permite(self):
        # É onde estão os dados financeiros e o webhook. Afrouxar ali para
        # consertar o admin seria pagar caro por um painel de staff.
        servidor = self.texto[: self.texto.index("location /admin/ {")]
        self.assertNotIn("'unsafe-eval'", _csp(servidor))

    def test_o_admin_repete_todos_os_cabecalhos(self):
        """`add_header` dentro de um location DESCARTA os herdados do server.

        Omitir um aqui não faz valer o do server: faz a resposta do admin sair
        sem ele. É o erro mais fácil de cometer neste arquivo.
        """
        for cabecalho in CABECALHOS:
            with self.subTest(cabecalho=cabecalho):
                self.assertIn(f"add_header {cabecalho}", self.admin)

    def test_as_duas_politicas_so_diferem_no_unsafe_eval(self):
        # Qualquer outra folga no admin seria acidental.
        servidor = self.texto[: self.texto.index("location /admin/ {")]
        self.assertEqual(_csp(self.admin).replace(" 'unsafe-eval'", ""), _csp(servidor))

    def test_o_admin_continua_sendo_servido_pelo_gunicorn(self):
        # Um location novo sem proxy_pass devolveria 404 no admin inteiro.
        self.assertIn("proxy_pass http://127.0.0.1:8015", self.admin)
