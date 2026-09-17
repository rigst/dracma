"""
Mensagens do Telegram, mídia baixada e o vínculo de conta.

Nada aqui interpreta conteúdo financeiro: este app é o transporte. Quem entende
a mensagem é `ai`, quem grava o lançamento é `carteira`.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

ALFABETO_CODIGO = "0123456789"


def gerar_codigo_pareamento() -> str:
    """6 dígitos. `secrets` e não `random`: é credencial de vínculo de conta."""
    return "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(6))


def gerar_token_pareamento() -> str:
    """Payload do deep link `t.me/bot?start=<token>`.

    Separado do código de 6 dígitos porque o uso é outro: ninguém digita este,
    ele viaja na URL. Sendo assim pode (e deve) ser largo o bastante para não
    valer a pena chutar. O Telegram aceita até 64 caracteres de [A-Za-z0-9_-],
    que é exatamente o alfabeto do `token_urlsafe`.
    """
    return secrets.token_urlsafe(16)


class ContaTelegram(models.Model):
    """Conversa do Telegram vinculada a um usuário.

    O vínculo é o que decide em qual espaço o lançamento cai. Sem ele, uma
    mensagem de conta desconhecida não vira transação nenhuma, só o convite
    para parear.
    """

    # O `chat.id` do Telegram. BigInteger e não Char: são inteiros de verdade e
    # os de grupo/canal são negativos e passam de 32 bits.
    chat_id = models.BigIntegerField("chat id", unique=True)
    # Só para reconhecer a pessoa no admin: o Telegram não expõe telefone, e
    # o @username é opcional e pode mudar a qualquer momento. Nunca use como
    # identidade; a identidade é o chat_id.
    username = models.CharField("@username", max_length=64, blank=True)
    primeiro_nome = models.CharField("primeiro nome", max_length=100, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="contas_telegram",
        null=True,
        blank=True,
    )
    verificado_em = models.DateTimeField("verificado em", null=True, blank=True)
    # Quando o Telegram respondeu 403 (a pessoa bloqueou o bot ou apagou a
    # conversa). Sem isto, todo alerta futuro sairia para um destino que nunca
    # entrega, uma chamada de rede por rodada do beat, para sempre.
    bloqueado_em = models.DateTimeField("bloqueado em", null=True, blank=True)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)

    # Onde a pessoa está no roteiro de primeiros passos. Guardado por CONVERSA
    # e não por usuário porque é a conversa que tem o roteiro: num casal, quem
    # entrou depois também precisa aprender a usar.
    onboarding_etapa = models.PositiveSmallIntegerField("etapa do onboarding", default=0)

    class Meta:
        verbose_name = "conta do Telegram"
        verbose_name_plural = "contas do Telegram"

    def __str__(self) -> str:
        return f"@{self.username}" if self.username else str(self.chat_id)

    @property
    def vinculado(self) -> bool:
        return self.usuario_id is not None and self.verificado_em is not None

    @property
    def alcancavel(self) -> bool:
        """Vinculado e sem bloqueio: só assim vale a pena tentar um envio."""
        return self.vinculado and self.bloqueado_em is None


class CodigoPareamento(models.Model):
    """Credencial de vínculo, gerada no portal e consumida no Telegram.

    Carrega as duas formas do mesmo pareamento: o `token`, que viaja no deep
    link e é entregue sozinho pelo `/start`, e o `codigo` de 6 dígitos, que
    existe para quem já está com o bot aberto em outro aparelho e prefere
    digitar.
    """

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pareamentos"
    )
    codigo = models.CharField("código", max_length=8, default=gerar_codigo_pareamento)
    token = models.CharField(
        "token do deep link", max_length=64, default=gerar_token_pareamento, unique=True
    )
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    usado_em = models.DateTimeField("usado em", null=True, blank=True)
    expira_em = models.DateTimeField("expira em")

    class Meta:
        verbose_name = "código de pareamento"
        verbose_name_plural = "códigos de pareamento"
        indexes = [
            models.Index(fields=["codigo", "usado_em"]),
            models.Index(fields=["token", "usado_em"]),
        ]

    def __str__(self) -> str:
        return f"{self.codigo} · {self.usuario}"

    def valido(self) -> bool:
        return self.usado_em is None and timezone.now() < self.expira_em


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
        SISTEMA = "sistema", "Sistema"

    class Status(models.TextChoices):
        RECEBIDA = "recebida", "Recebida"
        PROCESSANDO = "processando", "Processando"
        RESPONDIDA = "respondida", "Respondida"
        ERRO = "erro", "Erro"
        IGNORADA = "ignorada", "Ignorada"

    conta = models.ForeignKey(
        ContaTelegram, on_delete=models.CASCADE, related_name="mensagens", null=True, blank=True
    )
    # Preenchido quando a mensagem veio do console do portal, que não tem
    # conversa no Telegram. Um dos dois sempre está preenchido.
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
    # Chave de idempotência. O `message_id` do Telegram só é único DENTRO de
    # uma conversa, então sozinho ele colidiria entre usuários; o que guardamos
    # é "<chat_id>:<message_id>", ver bot.webhook.identificador().
    id_externo = models.CharField("id externo", max_length=128, unique=True, null=True, blank=True)
    texto = models.TextField("texto", blank=True)
    # Transcrição do áudio, quando houver. Fica separada do `texto` para o log
    # continuar mostrando o que chegou de fato.
    transcricao = models.TextField("transcrição", blank=True)
    status = models.CharField("status", max_length=20, choices=Status, default=Status.RECEBIDA)
    erro = models.TextField("erro", blank=True)
    # Payload cru do webhook, para depurar sem precisar reproduzir no Telegram.
    payload = models.JSONField("payload", null=True, blank=True)
    # As mensagens do turno que gerou esta resposta, com os blocos `tool_use` e
    # `tool_result`. É o que o próximo turno reproduz como histórico.
    #
    # Guardado porque o histórico só de texto mentia: o modelo lia a própria
    # confirmação (“Uber de R$ 20 registrado ✅”) como narração, não como prova
    # de que a escrita aconteceu, e refazia a tool, em produção isso duplicou
    # um lançamento e ainda confirmou o ajuste que não tinha feito.
    turno = models.JSONField("turno", null=True, blank=True)
    criada_em = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"
        ordering = ["criada_em"]
        indexes = [
            models.Index(fields=["conta", "criada_em"]),
            models.Index(fields=["usuario", "criada_em"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_direcao_display()} · {self.texto[:40] or self.get_tipo_display()}"

    @property
    def conteudo(self) -> str:
        """O que o agente deve ler: a transcrição quando existe, senão o texto."""
        return self.transcricao or self.texto


class Midia(models.Model):
    """Arquivo baixado da Bot API.

    O update traz só um `file_id`; o binário vem em duas chamadas (`getFile` e
    depois o download). O `file_path` devolvido expira em ~1h, por isso o que
    guardamos é o arquivo, não a URL.
    """

    mensagem = models.OneToOneField(Mensagem, on_delete=models.CASCADE, related_name="midia")
    file_id = models.CharField("file id", max_length=256, blank=True)
    mime_type = models.CharField("mime type", max_length=100, blank=True)
    arquivo = models.FileField("arquivo", upload_to="bot/%Y/%m/")
    tamanho = models.PositiveIntegerField("tamanho (bytes)", default=0)
    duracao_s = models.PositiveIntegerField("duração (s)", null=True, blank=True)
    criada_em = models.DateTimeField("criada em", auto_now_add=True)

    class Meta:
        verbose_name = "mídia"
        verbose_name_plural = "mídias"

    def __str__(self) -> str:
        return f"{self.mime_type} · {self.tamanho} bytes"
