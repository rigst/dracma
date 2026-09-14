"""
Mensagens do WhatsApp, mídia baixada e a janela de atendimento da Meta.

Nada aqui interpreta conteúdo financeiro: este app é o transporte. Quem entende
a mensagem é `ai`, quem grava o lançamento é `carteira`.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

ALFABETO_CODIGO = "0123456789"


def gerar_codigo_pareamento() -> str:
    """6 dígitos. `secrets` e não `random`: é credencial de vínculo de conta."""
    return "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(6))


class NumeroWhatsApp(models.Model):
    """Telefone vinculado a um usuário.

    O vínculo é o que decide em qual espaço o lançamento cai. Sem ele, uma
    mensagem de número desconhecido não vira transação nenhuma — só o convite
    para parear.
    """

    # E.164 sem o "+", que é como a Meta entrega no webhook (ex.: 5511999998888).
    numero = models.CharField("número", max_length=20, unique=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="numeros",
        null=True,
        blank=True,
    )
    verificado_em = models.DateTimeField("verificado em", null=True, blank=True)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    class Meta:
        verbose_name = "número de WhatsApp"
        verbose_name_plural = "números de WhatsApp"

    def __str__(self) -> str:
        return self.numero

    @property
    def vinculado(self) -> bool:
        return self.usuario_id is not None and self.verificado_em is not None


class CodigoPareamento(models.Model):
    """Código de 6 dígitos gerado no portal e confirmado pelo WhatsApp."""

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pareamentos"
    )
    codigo = models.CharField("código", max_length=8, default=gerar_codigo_pareamento)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    usado_em = models.DateTimeField("usado em", null=True, blank=True)
    expira_em = models.DateTimeField("expira em")

    class Meta:
        verbose_name = "código de pareamento"
        verbose_name_plural = "códigos de pareamento"
        indexes = [models.Index(fields=["codigo", "usado_em"])]

    def __str__(self) -> str:
        return f"{self.codigo} · {self.usuario}"

    def valido(self) -> bool:
        return self.usado_em is None and timezone.now() < self.expira_em


class JanelaAtendimento(models.Model):
    """Quando o número mandou a última mensagem.

    A Meta só permite resposta em formato livre dentro de 24h desde o último
    inbound do usuário; fora disso, só template aprovado. Guardar esse
    carimbo por número é o que permite a decisão ser tomada num lugar só
    (`zap.janela`), em vez de um `if` espalhado por cada ponto de envio.
    """

    numero = models.OneToOneField(
        NumeroWhatsApp, on_delete=models.CASCADE, related_name="janela"
    )
    ultimo_inbound_em = models.DateTimeField("último inbound em")

    class Meta:
        verbose_name = "janela de atendimento"
        verbose_name_plural = "janelas de atendimento"

    def __str__(self) -> str:
        return f"{self.numero} até {self.expira_em:%d/%m %H:%M}"

    @property
    def expira_em(self):
        return self.ultimo_inbound_em + timedelta(hours=settings.WHATSAPP_JANELA_HORAS)

    @property
    def aberta(self) -> bool:
        return timezone.now() < self.expira_em


class Mensagem(models.Model):
    """Log bruto de tudo que entra e sai, por qualquer canal."""

    class Direcao(models.TextChoices):
        ENTRADA = "entrada", "Entrada"
        SAIDA = "saida", "Saída"

    class Tipo(models.TextChoices):
        TEXTO = "texto", "Texto"
        AUDIO = "audio", "Áudio"
        IMAGEM = "imagem", "Imagem"
        DOCUMENTO = "documento", "Documento"
        TEMPLATE = "template", "Template"
        SISTEMA = "sistema", "Sistema"

    class Status(models.TextChoices):
        RECEBIDA = "recebida", "Recebida"
        PROCESSANDO = "processando", "Processando"
        RESPONDIDA = "respondida", "Respondida"
        ERRO = "erro", "Erro"
        IGNORADA = "ignorada", "Ignorada"

    numero = models.ForeignKey(
        NumeroWhatsApp, on_delete=models.CASCADE, related_name="mensagens", null=True, blank=True
    )
    # Preenchido quando a mensagem veio do console do portal, que não tem
    # número. Um dos dois sempre está preenchido.
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mensagens",
        null=True,
        blank=True,
    )
    canal = models.CharField("canal", max_length=20, default="console")
    direcao = models.CharField("direção", max_length=10, choices=Direcao)
    tipo = models.CharField("tipo", max_length=20, choices=Tipo, default=Tipo.TEXTO)
    # Identificador da mensagem na Meta. É a chave de idempotência: a Meta
    # reenvia o mesmo webhook, e sem `unique` a mesma fala vira duas transações.
    wamid = models.CharField("wamid", max_length=128, unique=True, null=True, blank=True)
    texto = models.TextField("texto", blank=True)
    # Transcrição do áudio, quando houver. Fica separada do `texto` para o log
    # continuar mostrando o que chegou de fato.
    transcricao = models.TextField("transcrição", blank=True)
    status = models.CharField("status", max_length=20, choices=Status, default=Status.RECEBIDA)
    erro = models.TextField("erro", blank=True)
    # Payload cru do webhook, para depurar sem precisar reproduzir na Meta.
    payload = models.JSONField("payload", null=True, blank=True)
    criada_em = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"
        ordering = ["criada_em"]
        indexes = [
            models.Index(fields=["numero", "criada_em"]),
            models.Index(fields=["usuario", "criada_em"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_direcao_display()} · {self.texto[:40] or self.get_tipo_display()}"

    @property
    def conteudo(self) -> str:
        """O que o agente deve ler: a transcrição quando existe, senão o texto."""
        return self.transcricao or self.texto


class Midia(models.Model):
    """Arquivo baixado da Graph API.

    O webhook traz só um `media_id`; o binário vem em uma segunda chamada
    autenticada. Guardamos o arquivo porque a URL da Meta expira em minutos.
    """

    mensagem = models.OneToOneField(Mensagem, on_delete=models.CASCADE, related_name="midia")
    media_id = models.CharField("media id", max_length=128, blank=True)
    mime_type = models.CharField("mime type", max_length=100, blank=True)
    arquivo = models.FileField("arquivo", upload_to="zap/%Y/%m/")
    tamanho = models.PositiveIntegerField("tamanho (bytes)", default=0)
    duracao_s = models.PositiveIntegerField("duração (s)", null=True, blank=True)
    criada_em = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        verbose_name = "mídia"
        verbose_name_plural = "mídias"

    def __str__(self) -> str:
        return f"{self.mime_type} · {self.tamanho} bytes"
