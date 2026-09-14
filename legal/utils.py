"""Utilidades compartilhadas do app `legal`."""

import hashlib
import re

import nh3
from markdown import markdown

# Tags que fazem sentido num documento legal. Tudo fora desta lista é removido
# pelo nh3 — o texto vem do admin, mas é servido para o público e vira prova.
TAGS_PERMITIDAS = {
    "h1",
    "h2",
    "h3",
    "h4",
    "p",
    "br",
    "hr",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "ul",
    "ol",
    "li",
    "a",
    "blockquote",
    "code",
    "pre",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
}

# `rel` fica fora de propósito: quem o gerencia é o link_rel do nh3, que injeta
# noopener/noreferrer em todo link — declarar os dois é erro de configuração.
ATRIBUTOS_PERMITIDOS = {"a": {"href", "title", "target"}}


def ip_do_request(request):
    """IP do cliente, confiando apenas no que o nginx acrescentou.

    Atrás de `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`, o nginx
    ANEXA o IP que ele mesmo observou ao final do cabeçalho. Os itens anteriores
    vieram do cliente e são forjáveis, então o único confiável é o último — pegar
    o primeiro (como faziam trilhas e questões) deixa qualquer um escolher o IP
    que ficará gravado na prova de aceite.
    """
    encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if encaminhado:
        ultimo = encaminhado.split(",")[-1].strip()
        if ultimo:
            return ultimo
    return request.META.get("REMOTE_ADDR", "") or ""


def normalizar_texto(texto):
    """Normaliza o Markdown antes do hash.

    Sem isso, um CRLF ou um espaço em branco no fim da linha mudaria o sha256 sem
    mudar uma vírgula do que a pessoa leu.
    """
    texto = (texto or "").replace("\r\n", "\n").replace("\r", "\n")
    linhas = [linha.rstrip() for linha in texto.split("\n")]
    return "\n".join(linhas).strip() + "\n"


def calcular_sha256(texto):
    return hashlib.sha256(normalizar_texto(texto).encode("utf-8")).hexdigest()


def renderizar_markdown(texto):
    """Markdown -> HTML sanitizado, pronto para exibição."""
    bruto = markdown(
        normalizar_texto(texto),
        extensions=["extra", "sane_lists"],
        output_format="html",
    )
    return nh3.clean(
        bruto,
        tags=TAGS_PERMITIDAS,
        attributes=ATRIBUTOS_PERMITIDOS,
        link_rel="noopener noreferrer",
    )


def proxima_versao(versao_atual):
    """Sugere a próxima versão a partir da atual (`1.0` -> `1.1`, `2.9` -> `2.10`)."""
    match = re.fullmatch(r"(\d+)\.(\d+)", (versao_atual or "").strip())
    if match:
        maior, menor = int(match.group(1)), int(match.group(2))
        return f"{maior}.{menor + 1}"
    return f"{versao_atual}-nova" if versao_atual else "1.0"
