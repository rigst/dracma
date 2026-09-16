"""Canal da Bot API do Telegram.

Bem mais simples do que a Cloud API da Meta que este app usava antes, e a
diferença não é de estilo — são três regras da plataforma que somem:

1. **Não há janela de 24h.** Uma vez que a pessoa deu `/start`, o bot pode
   escrever quando quiser. Todo o aparato de janela e de template aprovado
   deixou de existir; um alerta proativo é só uma mensagem.
2. **Não há allowlist nem revisão de número.** O bot nasce funcionando para
   qualquer pessoa, o que torna o canal `console` uma conveniência de
   desenvolvimento em vez de uma necessidade da demo.
3. **Não há assinatura HMAC no webhook.** A autenticidade vem de um segredo
   que nós escolhemos e o Telegram repete no cabeçalho — ver `bot.webhook`.

O que continua igual: mídia vem em duas etapas e o link de download expira.
"""

from __future__ import annotations

import logging
import mimetypes
import time

import httpx
from django.conf import settings

from .base import CanalMensagem, MensagemEnviada, MidiaBaixada

logger = logging.getLogger(__name__)

# O `connect` é o orçamento do TCP + handshake TLS. Daqui o normal é ~0,4s;
# 10s é folga para um handshake lento sem deixar o worker preso.
TIMEOUT = httpx.Timeout(20.0, connect=10.0)

# Teto da própria plataforma para o texto de uma mensagem.
MAX_CHARS = 4096

# Uma falha de rede não pode custar a resposta da pessoa. Sem repetir, um
# handshake TLS que estoura o tempo — coisa que acontece — deixa a fala do
# agente gravada como erro e a pessoa esperando para sempre por algo que
# nunca vai chegar. Três tentativas cobrem o blip de segundos sem segurar
# o worker por muito tempo.
TENTATIVAS = 3
ESPERA_BASE = 0.5


class TelegramCanal(CanalMensagem):
    nome = "telegram"

    def __init__(self, cliente: httpx.Client | None = None):
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.base = f"{settings.TELEGRAM_API_BASE}/bot{self.token}"
        self.base_arquivos = f"{settings.TELEGRAM_API_BASE}/file/bot{self.token}"
        self._cliente = cliente

    # -- infraestrutura -----------------------------------------------------

    @property
    def cliente(self) -> httpx.Client:
        if self._cliente is None:
            self._cliente = httpx.Client(timeout=TIMEOUT)
        return self._cliente

    def _chamar(self, metodo: str, **parametros) -> dict:
        """Uma chamada da Bot API. Devolve o `result`; levanta em caso de erro.

        A Bot API responde 200 com `{"ok": false}` em alguns casos, então
        conferir o status HTTP não basta: quem manda é o campo `ok`.

        Repete o que é transitório — falha de rede, 429 e 5xx — e desiste na
        hora do que é definitivo, como o 403 de quem bloqueou o bot ou um 400
        de payload inválido. Insistir nesses só atrasaria a conclusão que já
        se tem.
        """
        for tentativa in range(1, TENTATIVAS + 1):
            ultima = tentativa == TENTATIVAS
            try:
                resposta = self.cliente.post(f"{self.base}/{metodo}", json=parametros)
            except httpx.HTTPError as exc:
                if ultima:
                    raise
                espera = ESPERA_BASE * 2 ** (tentativa - 1)
                logger.info(
                    "%s falhou na rede (%s); repetindo em %.1fs [%d/%d]",
                    metodo,
                    exc,
                    espera,
                    tentativa,
                    TENTATIVAS,
                )
                time.sleep(espera)
                continue

            dados = resposta.json()
            if dados.get("ok"):
                return dados.get("result") or {}

            erro = TelegramErro(
                dados.get("error_code", resposta.status_code),
                dados.get("description", resposta.text[:500]),
                (dados.get("parameters") or {}).get("retry_after"),
            )
            if ultima or not erro.transitorio:
                raise erro

            # No 429 quem manda é o `retry_after` do Telegram: chutar menos que
            # ele só gasta outra tentativa para levar o mesmo 429 de volta.
            espera = erro.espera_s or ESPERA_BASE * 2 ** (tentativa - 1)
            logger.info(
                "%s recusado (%s); repetindo em %.1fs [%d/%d]",
                metodo,
                erro.codigo,
                espera,
                tentativa,
                TENTATIVAS,
            )
            time.sleep(espera)

        raise AssertionError("inalcançável: o laço sempre volta ou levanta")

    # -- envio --------------------------------------------------------------

    def enviar_texto(self, destino, texto: str) -> MensagemEnviada:
        if not settings.TELEGRAM_ENABLED:
            logger.info("TELEGRAM_ENABLED=False: envio ignorado.")
            return MensagemEnviada(entregue=False, erro="Telegram desligado")

        try:
            # Sem `parse_mode` de propósito. O texto vem do agente e é livre:
            # um `_` ou um `.` soltos bastam para o MarkdownV2 recusar a
            # mensagem inteira com 400, e a pessoa fica sem resposta nenhuma.
            # Texto puro sempre entrega.
            resultado = self._chamar(
                "sendMessage",
                chat_id=int(destino),
                text=texto[:MAX_CHARS],
                # Os textos têm valores e códigos, nunca links que valha a pena
                # expandir, e a prévia atrasa a entrega.
                link_preview_options={"is_disabled": True},
            )
        except TelegramErro as exc:
            if exc.bloqueado:
                logger.info("Conversa %s não alcançável: %s", destino, exc.descricao)
                return MensagemEnviada(entregue=False, erro=exc.descricao, bloqueado=True)
            logger.warning("Telegram recusou o envio (%s): %s", exc.codigo, exc.descricao)
            return MensagemEnviada(entregue=False, erro=exc.descricao)
        except httpx.HTTPError as exc:
            logger.warning("Falha de rede ao falar com o Telegram: %s", exc)
            return MensagemEnviada(entregue=False, erro=str(exc))

        return MensagemEnviada(id_externo=identificador(resultado))

    # -- mídia --------------------------------------------------------------

    def baixar_midia(self, file_id: str, mime_hint: str = "") -> MidiaBaixada:
        """Duas chamadas: `getFile` e depois o binário.

        O `file_path` devolvido no primeiro passo expira em cerca de uma hora —
        não adianta guardá-lo em vez do arquivo.
        """
        arquivo = self._chamar("getFile", file_id=file_id)

        tamanho = int(arquivo.get("file_size") or 0)
        if tamanho > settings.TELEGRAM_MAX_MIDIA_BYTES:
            raise ValueError(f"Mídia de {tamanho} bytes acima do teto configurado.")

        caminho = arquivo.get("file_path") or ""
        binario = self.cliente.get(f"{self.base_arquivos}/{caminho}")
        binario.raise_for_status()
        conteudo = binario.content

        if len(conteudo) > settings.TELEGRAM_MAX_MIDIA_BYTES:
            # O `file_size` nem sempre vem; esta é a checagem que de fato
            # protege o disco e a memória do worker.
            raise ValueError(f"Mídia de {len(conteudo)} bytes acima do teto configurado.")

        return MidiaBaixada(
            conteudo=conteudo,
            mime_type=mime_hint or _mime_do_caminho(caminho),
            tamanho=len(conteudo),
        )


class TelegramErro(Exception):
    """Resposta com `ok: false`."""

    def __init__(self, codigo: int, descricao: str, retry_after: int | None = None):
        self.codigo = codigo
        self.descricao = descricao
        # Só o 429 traz isto, em `parameters.retry_after`.
        self.espera_s = retry_after
        super().__init__(f"{codigo}: {descricao}")

    @property
    def bloqueado(self) -> bool:
        """403 é permanente: bot bloqueado, conversa apagada, usuário desativado.

        Repetir não resolve nenhum dos três, e é o caso que justifica marcar a
        conta em vez de tentar de novo a cada rodada do beat.
        """
        return self.codigo == 403

    @property
    def transitorio(self) -> bool:
        """Vale repetir: excesso de chamadas (429) ou problema do lado deles (5xx)."""
        return self.codigo == 429 or self.codigo >= 500


def identificador(mensagem: dict) -> str:
    """Chave estável de uma mensagem: "<chat_id>:<message_id>".

    O `message_id` do Telegram só é único dentro de uma conversa. Sozinho, ele
    colidiria entre usuários — e como o campo é `unique`, a mensagem de um
    seria descartada como repetição da de outro.
    """
    chat = (mensagem.get("chat") or {}).get("id")
    msg = mensagem.get("message_id")
    if chat is None or msg is None:
        return ""
    return f"{chat}:{msg}"


def _mime_do_caminho(caminho: str) -> str:
    """O `getFile` não devolve mime_type; o `file_path` traz a extensão.

    Usado só quando o update não trouxe um mime melhor — em foto, por exemplo,
    onde o Telegram simplesmente não informa nenhum.
    """
    adivinhado, _ = mimetypes.guess_type(caminho)
    return adivinhado or "application/octet-stream"
