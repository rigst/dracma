"""Canal da WhatsApp Cloud API (Graph API da Meta).

Integração OFICIAL, e não uma biblioteca que fala o protocolo do WhatsApp Web:
essas são não-oficiais, arriscam o ban do número e caem em silêncio quando a
sessão morre. Aqui não há sessão nem container extra — é HTTPS contra a Graph
API, com o webhook servido pelo nginx que já existe.

Duas particularidades da plataforma que o código precisa respeitar:

1. **Mídia vem em duas etapas.** O webhook entrega só um `media_id`; o binário
   exige uma chamada para pegar a URL e outra, autenticada, para baixar. A URL
   expira em minutos, por isso guardamos o arquivo.
2. **Fora da janela de 24h só passa template aprovado.** A decisão fica em
   `zap.janela`; aqui só existe o transporte dos dois formatos.
"""

from __future__ import annotations

import logging

import httpx
from django.conf import settings

from .base import CanalMensagem, MensagemEnviada, MidiaBaixada

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class CloudAPICanal(CanalMensagem):
    nome = "cloud_api"

    def __init__(self, cliente: httpx.Client | None = None):
        self.base = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}"
        self.phone_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self.token = settings.WHATSAPP_ACCESS_TOKEN
        self._cliente = cliente

    # -- infraestrutura -----------------------------------------------------

    @property
    def cliente(self) -> httpx.Client:
        if self._cliente is None:
            self._cliente = httpx.Client(timeout=TIMEOUT)
        return self._cliente

    @property
    def cabecalhos(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _postar(self, corpo: dict) -> MensagemEnviada:
        if not settings.WHATSAPP_ENABLED:
            logger.info("WHATSAPP_ENABLED=False: envio ignorado.")
            return MensagemEnviada(entregue=False, erro="WhatsApp desligado")

        url = f"{self.base}/{self.phone_id}/messages"
        try:
            resposta = self.cliente.post(url, json=corpo, headers=self.cabecalhos)
            resposta.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # O corpo do erro da Meta diz exatamente o que houve (janela
            # fechada, template não aprovado, número fora da allowlist). Sem
            # ele, o log vira "400 Bad Request" e não se descobre nada.
            detalhe = exc.response.text[:500]
            logger.warning("Meta recusou o envio (%s): %s", exc.response.status_code, detalhe)
            return MensagemEnviada(entregue=False, erro=detalhe)
        except httpx.HTTPError as exc:
            logger.warning("Falha de rede ao falar com a Meta: %s", exc)
            return MensagemEnviada(entregue=False, erro=str(exc))

        dados = resposta.json()
        mensagens = dados.get("messages") or [{}]
        return MensagemEnviada(id_externo=mensagens[0].get("id", ""))

    # -- envio --------------------------------------------------------------

    def enviar_texto(self, destino, texto: str) -> MensagemEnviada:
        return self._postar(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": str(destino),
                "type": "text",
                # preview_url desligado: o texto tem valores e códigos, nunca
                # links que valha a pena expandir, e a prévia atrasa a entrega.
                "text": {"preview_url": False, "body": texto[:4096]},
            }
        )

    def enviar_template(self, destino, template: str, parametros: list[str]) -> MensagemEnviada:
        return self._postar(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": str(destino),
                "type": "template",
                "template": {
                    "name": template,
                    "language": {"code": settings.WHATSAPP_TEMPLATE_IDIOMA},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": str(p)} for p in parametros],
                        }
                    ],
                },
            }
        )

    def suporta_template(self) -> bool:
        return True

    # -- mídia --------------------------------------------------------------

    def baixar_midia(self, media_id: str) -> MidiaBaixada:
        """Duas chamadas: metadados e depois o binário.

        A URL devolvida no primeiro passo é de uso único e expira em minutos —
        não adianta guardá-la em vez do arquivo.
        """
        meta = self.cliente.get(f"{self.base}/{media_id}", headers=self.cabecalhos)
        meta.raise_for_status()
        dados = meta.json()

        tamanho = int(dados.get("file_size") or 0)
        if tamanho > settings.WHATSAPP_MAX_MIDIA_BYTES:
            raise ValueError(f"Mídia de {tamanho} bytes acima do teto configurado.")

        # O download exige o mesmo Bearer: a URL não é pública.
        binario = self.cliente.get(dados["url"], headers=self.cabecalhos)
        binario.raise_for_status()
        conteudo = binario.content

        if len(conteudo) > settings.WHATSAPP_MAX_MIDIA_BYTES:
            # O `file_size` dos metadados nem sempre vem; esta é a checagem que
            # de fato protege o disco e a memória do worker.
            raise ValueError(f"Mídia de {len(conteudo)} bytes acima do teto configurado.")

        return MidiaBaixada(
            conteudo=conteudo,
            mime_type=dados.get("mime_type", "application/octet-stream"),
            tamanho=len(conteudo),
        )
