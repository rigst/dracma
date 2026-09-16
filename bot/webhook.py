"""Validação e parsing do webhook da Bot API.

Separado das views de propósito: a view precisa ser burra e rápida, e a lógica
de autenticidade e formato merece teste próprio.
"""

from __future__ import annotations

import hmac
import logging

from django.conf import settings

from .canais.telegram import identificador

logger = logging.getLogger(__name__)

# Mapa do campo presente no update para o nosso tipo.
TIPOS = {
    "text": "texto",
    "voice": "audio",
    "audio": "audio",
    "photo": "imagem",
    "document": "documento",
}

PREFIXO_START = "/start"


def token_valido(cabecalho: str | None) -> bool:
    """Confere o X-Telegram-Bot-Api-Secret-Token.

    O Telegram não assina o corpo como a Meta fazia; o mecanismo oficial é um
    segredo que nós escolhemos no `setWebhook` e ele repete em todo POST. Sem
    isso, qualquer um que descubra a URL injeta transação em conta alheia.

    A comparação usa `compare_digest`. Um `==` comum vaza, pelo tempo de
    resposta, quantos bytes iniciais do segredo o atacante acertou.
    """
    esperado = settings.TELEGRAM_WEBHOOK_SECRET
    if not esperado:
        logger.error("TELEGRAM_WEBHOOK_SECRET vazio: recusando o webhook.")
        return False
    if not cabecalho:
        return False
    return hmac.compare_digest(cabecalho, esperado)


def extrair_mensagens(payload: dict) -> list[dict]:
    """Normaliza o update numa lista de mensagens.

    O Telegram manda UM update por POST, então a lista tem no máximo um item —
    devolver lista mesmo assim mantém a view igual à de qualquer outro canal e
    dispensa um caso especial para "não havia mensagem nenhuma".

    `edited_message` fica de fora: reprocessar uma edição criaria um segundo
    lançamento para a mesma fala.
    """
    bruta = payload.get("message")
    if not bruta:
        return []

    item = _normalizar(bruta)
    return [item] if item else []


def comando_start(texto: str) -> str | None:
    """Payload do `/start`, quando a mensagem é esse comando.

    Devolve "" para um `/start` pelado (a pessoa abriu o bot pela busca, sem
    deep link) e None quando não é o comando. A distinção importa: o primeiro
    merece o convite ao pareamento, o segundo segue para o agente.
    """
    texto = (texto or "").strip()
    if texto != PREFIXO_START and not texto.startswith(f"{PREFIXO_START} "):
        return None
    return texto[len(PREFIXO_START) :].strip()


def _normalizar(bruta: dict) -> dict | None:
    chat = bruta.get("chat") or {}

    # Só conversa privada. Um bot de finanças pessoais num grupo exporia o
    # extrato de alguém para o grupo inteiro, e o pareamento (que vincula a
    # conversa a UMA conta) não tem sentido com vários remetentes.
    if chat.get("type") != "private":
        logger.info("Update de chat %s ignorado.", chat.get("type"))
        return None

    campo = next((c for c in TIPOS if c in bruta), None)
    if campo is None:
        # Sticker, localização, contato, vídeo, enquete... nada que vire
        # lançamento. Ignorar em silêncio é o certo: o Telegram não deve
        # receber erro por mandar algo que não tratamos.
        logger.info("Tipo de mensagem ignorado em %s.", bruta.get("message_id"))
        return None

    autor = bruta.get("from") or {}
    item = {
        "id_externo": identificador(bruta),
        "chat_id": chat.get("id"),
        "username": autor.get("username") or "",
        "primeiro_nome": autor.get("first_name") or "",
        "tipo": TIPOS[campo],
        "texto": "",
        "file_id": "",
        "mime_type": "",
        "duracao_s": None,
    }

    if campo == "text":
        item["texto"] = bruta.get("text") or ""
    else:
        corpo = _corpo_da_midia(bruta, campo)
        item["file_id"] = corpo.get("file_id", "")
        # Foto não traz mime_type nenhum; o canal deduz pela extensão do
        # file_path no download.
        item["mime_type"] = corpo.get("mime_type", "")
        # Legenda da imagem/documento: "mercado do mês" junto da foto do
        # comprovante é contexto que o agente usa.
        item["texto"] = bruta.get("caption") or ""
        item["duracao_s"] = corpo.get("duration")

        if not item["file_id"]:
            logger.warning("Mídia sem file_id em %s; descartada.", bruta.get("message_id"))
            return None

    if not item["id_externo"] or item["chat_id"] is None:
        logger.warning("Mensagem sem identificador ou remetente; descartada.")
        return None

    return item


def _corpo_da_midia(bruta: dict, campo: str) -> dict:
    """O corpo do anexo. `photo` é o caso torto.

    Ela vem como uma LISTA de tamanhos, do menor ao maior. O último é o de
    maior resolução — que é o que se quer para ler um comprovante: as
    miniaturas ficam ilegíveis e o modelo erra o valor.
    """
    corpo = bruta.get(campo)
    if campo == "photo":
        return (corpo or [{}])[-1]
    return corpo or {}
