"""Monta o conteúdo multimodal que vai para a Claude.

Imagem e PDF vão como blocos nativos — a API os lê direto, sem OCR nosso.
Áudio não: a API não aceita áudio, então ele chega aqui já transcrito.
"""

from __future__ import annotations

import base64
import logging

logger = logging.getLogger(__name__)

# A API aceita estes formatos de imagem; qualquer outro precisa virar um deles.
IMAGENS = {"image/jpeg", "image/png", "image/gif", "image/webp"}
PDF = "application/pdf"


def montar(mensagem) -> str | list[dict]:
    """str quando é só texto; lista de blocos quando há mídia."""
    texto = mensagem.conteudo.strip()
    midia = getattr(mensagem, "midia", None)

    if midia is None or not midia.arquivo:
        return texto or "(mensagem vazia)"

    bloco = _bloco_de_midia(midia)
    if bloco is None:
        return texto or "(não consegui ler esse arquivo)"

    # O bloco de mídia vem ANTES do texto: é a ordem recomendada e a que o
    # modelo lê melhor quando o texto se refere à imagem ("mercado do mês").
    instrucao = texto or (
        "Leia este comprovante e registre o lançamento correspondente."
        if bloco["type"] == "image"
        else "Leia este documento e registre os lançamentos que encontrar."
    )
    return [bloco, {"type": "text", "text": instrucao}]


def _bloco_de_midia(midia) -> dict | None:
    mime = (midia.mime_type or "").split(";")[0].strip()

    if mime not in IMAGENS and mime != PDF:
        logger.info("Mídia de tipo %s não vai para a Claude.", mime)
        return None

    try:
        with midia.arquivo.open("rb") as arquivo:
            dados = base64.standard_b64encode(arquivo.read()).decode("utf-8")
    except OSError as exc:
        logger.warning("Não consegui ler a mídia %s: %s", midia.pk, exc)
        return None

    if mime == PDF:
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": PDF, "data": dados},
        }
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mime, "data": dados},
    }
