"""Validação e parsing do webhook da Meta.

Separado das views de propósito: a view precisa ser burra e rápida, e a lógica
de assinatura e formato merece teste próprio.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# Mapa do `type` do webhook para o nosso.
TIPOS = {
    "text": "texto",
    "audio": "audio",
    "voice": "audio",
    "image": "imagem",
    "document": "documento",
}


def assinatura_valida(corpo: bytes, cabecalho: str | None) -> bool:
    """Confere o X-Hub-Signature-256.

    Duas armadilhas:

    - O HMAC é sobre o corpo CRU. Reparsear o JSON e re-serializar muda espaços
      e ordem de chaves, e a assinatura nunca mais bate.
    - A comparação usa `compare_digest`. Um `==` comum vaza, pelo tempo de
      resposta, quantos bytes iniciais do digest o atacante acertou.
    """
    segredo = settings.WHATSAPP_APP_SECRET
    if not segredo:
        logger.error("WHATSAPP_APP_SECRET vazio: recusando o webhook.")
        return False
    if not cabecalho or not cabecalho.startswith("sha256="):
        return False

    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho.removeprefix("sha256="))


def verificar_handshake(parametros) -> str | None:
    """Handshake GET da assinatura do webhook.

    A Meta chama uma vez, na configuração, e espera o `hub.challenge` de volta
    em texto puro quando o `hub.verify_token` bate com o combinado.
    """
    esperado = settings.WHATSAPP_VERIFY_TOKEN
    modo = parametros.get("hub.mode")
    token = parametros.get("hub.verify_token")
    desafio = parametros.get("hub.challenge")

    if modo == "subscribe" and esperado and token and hmac.compare_digest(token, esperado):
        return desafio or ""
    return None


def extrair_mensagens(payload: dict) -> list[dict]:
    """Achata o payload da Meta numa lista de mensagens.

    O formato é aninhado em quatro níveis (entry > changes > value > messages) e
    um POST pode trazer várias mensagens de vários números. Tratar só a
    primeira perde lançamento quando a pessoa manda três áudios seguidos.

    Eventos de status de entrega (`statuses`) são ignorados aqui: não são
    mensagens do usuário e não abrem a janela de 24h.
    """
    encontradas: list[dict] = []

    for entrada in payload.get("entry", []) or []:
        for mudanca in entrada.get("changes", []) or []:
            valor = mudanca.get("value") or {}
            for bruta in valor.get("messages", []) or []:
                item = _normalizar(bruta)
                if item:
                    encontradas.append(item)

    return encontradas


def _normalizar(bruta: dict) -> dict | None:
    tipo_meta = bruta.get("type")
    tipo = TIPOS.get(tipo_meta)
    if tipo is None:
        # Reação, localização, contato, sticker, botão... nada que vire
        # lançamento. Ignorar em silêncio é o certo: a Meta não deve receber
        # erro por mandar algo que não tratamos.
        logger.info("Tipo de mensagem ignorado: %s", tipo_meta)
        return None

    item = {
        "wamid": bruta.get("id", ""),
        "de": bruta.get("from", ""),
        "tipo": tipo,
        "texto": "",
        "media_id": "",
        "mime_type": "",
        "duracao_s": None,
    }

    if tipo == "texto":
        item["texto"] = (bruta.get("text") or {}).get("body", "")
    else:
        corpo = bruta.get(tipo_meta) or {}
        item["media_id"] = corpo.get("id", "")
        item["mime_type"] = corpo.get("mime_type", "")
        # Legenda da imagem/documento: "mercado do mês" junto da foto do
        # comprovante é contexto que o agente usa.
        item["texto"] = corpo.get("caption", "") or ""
        if "seconds" in corpo:
            item["duracao_s"] = corpo.get("seconds")

    if not item["wamid"] or not item["de"]:
        logger.warning("Mensagem sem wamid ou remetente; descartada.")
        return None

    return item
