"""Documentos legais versionados e registro de aceite.

O par de modelos aqui existe para responder, anos depois, a uma pergunta simples:
*esta pessoa aceitou exatamente qual texto, quando e de onde?* Por isso o texto é
congelado na publicação e o aceite guarda uma cópia do que importa, em vez de só
apontar para linhas que podem mudar.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from .utils import calcular_sha256, renderizar_markdown


class TipoDocumento(models.TextChoices):
    TERMOS = "termos", "Termos de Uso"
    PRIVACIDADE = "privacidade", "Política de Privacidade"


class StatusDocumento(models.TextChoices):
    RASCUNHO = "rascunho", "Rascunho"
    PUBLICADO = "publicado", "Publicado"
    ARQUIVADO = "arquivado", "Arquivado"


class OrigemAceite(models.TextChoices):
    CADASTRO = "cadastro", "Cadastro"
    VISITANTE = "visitante", "Entrada como visitante"
    REACEITE = "reaceite", "Re-aceite de nova versão"
    UPLOAD_ANONIMO = "upload_anonimo", "Uso anônimo"


class DocumentoLegalQuerySet(models.QuerySet):
    def publicados(self):
        return self.filter(status=StatusDocumento.PUBLICADO)

    def vigente(self, tipo):
        return self.publicados().filter(tipo=tipo).order_by("-publicado_em").first()


class DocumentoLegal(models.Model):
    """Uma versão de um documento legal.

    Uma vez publicado e aceito por alguém, o registro é imutável: o admin bloqueia
    edição e exclusão, e a única saída é duplicar como nova versão.
    """

    tipo = models.CharField("tipo", max_length=20, choices=TipoDocumento.choices)
    versao = models.CharField("versão", max_length=20)
    titulo = models.CharField("título", max_length=200)
    status = models.CharField(
        "situação",
        max_length=20,
        choices=StatusDocumento.choices,
        default=StatusDocumento.RASCUNHO,
    )
    corpo_md = models.TextField(
        "texto (Markdown)",
        help_text="Escreva em Markdown. O HTML é gerado e sanitizado na publicação.",
    )
    corpo_html = models.TextField("HTML publicado", blank=True, editable=False)
    sha256 = models.CharField("sha256", max_length=64, blank=True, editable=False, db_index=True)
    material = models.BooleanField(
        "mudança material",
        default=True,
        help_text=(
            "Marcado, obriga todos os usuários a aceitarem de novo. Desmarque apenas "
            "para correções de redação que não alterem direitos e obrigações."
        ),
    )
    vigente_desde = models.DateTimeField("vigente desde", null=True, blank=True)
    publicado_em = models.DateTimeField("publicado em", null=True, blank=True, editable=False)
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    objects = DocumentoLegalQuerySet.as_manager()

    class Meta:
        verbose_name = "documento legal"
        verbose_name_plural = "documentos legais"
        constraints = [
            models.UniqueConstraint(fields=["tipo", "versao"], name="legal_documento_tipo_versao"),
        ]
        ordering = ["tipo", "-criado_em"]

    def __str__(self):
        return f"{self.get_tipo_display()} v{self.versao} ({self.get_status_display()})"

    @property
    def publicado(self):
        return self.status == StatusDocumento.PUBLICADO

    @property
    def tem_aceites(self):
        return self.aceites.exists()

    @property
    def editavel(self):
        """Só rascunho sem aceite pode ser alterado."""
        return self.status == StatusDocumento.RASCUNHO and not self.tem_aceites

    def html_preview(self):
        """Render do rascunho, sem congelar nada — para a pré-visualização."""
        return renderizar_markdown(self.corpo_md)

    def publicar(self):
        """Congela o texto, arquiva a versão anterior e passa a valer.

        O `corpo_html` e o `sha256` são gravados aqui e nunca mais recalculados:
        é isso que garante que o texto exibido hoje é byte a byte o que foi aceito.
        """
        agora = timezone.now()
        DocumentoLegal.objects.filter(tipo=self.tipo, status=StatusDocumento.PUBLICADO).exclude(
            pk=self.pk
        ).update(status=StatusDocumento.ARQUIVADO, atualizado_em=agora)

        self.corpo_html = renderizar_markdown(self.corpo_md)
        self.sha256 = calcular_sha256(self.corpo_md)
        self.status = StatusDocumento.PUBLICADO
        self.publicado_em = agora
        if not self.vigente_desde:
            self.vigente_desde = agora
        self.save(
            update_fields=[
                "corpo_html",
                "sha256",
                "status",
                "publicado_em",
                "vigente_desde",
                "atualizado_em",
            ]
        )
        return self


class AceiteLegal(models.Model):
    """Registro imutável de um aceite.

    Nada aqui depende de o usuário continuar existindo: visitantes são apagados ao
    expirar (`usuarios.visitantes.limpar_dados_visitante` faz `user.delete()`), e a
    prova precisa sobreviver a isso. Daí o SET_NULL somado ao `usuario_label`.
    """

    documento = models.ForeignKey(
        DocumentoLegal,
        on_delete=models.PROTECT,
        related_name="aceites",
        verbose_name="documento",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="aceites_legais",
        verbose_name="usuário",
    )
    usuario_label = models.CharField(
        "identificação do usuário",
        max_length=200,
        blank=True,
        help_text="Congelado no momento do aceite; sobrevive à exclusão da conta.",
    )
    e_visitante = models.BooleanField("era visitante", default=False)
    session_key = models.CharField("sessão", max_length=40, blank=True, db_index=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    user_agent = models.TextField("navegador", blank=True)
    aceito_em = models.DateTimeField("aceito em", default=timezone.now, db_index=True)
    origem = models.CharField("origem", max_length=20, choices=OrigemAceite.choices)
    documento_sha256 = models.CharField("sha256 do texto aceito", max_length=64)
    evidencia = models.JSONField("evidência", default=dict, blank=True)

    class Meta:
        verbose_name = "aceite"
        verbose_name_plural = "aceites"
        ordering = ["-aceito_em"]
        indexes = [
            models.Index(fields=["documento", "usuario"]),
            models.Index(fields=["-aceito_em"]),
        ]

    def __str__(self):
        quem = self.usuario_label or "anônimo"
        return f"{quem} aceitou {self.documento} em {self.aceito_em:%d/%m/%Y %H:%M}"

    @property
    def integro(self):
        """O hash gravado no aceite ainda bate com o do documento?"""
        return bool(self.documento_sha256) and self.documento_sha256 == self.documento.sha256
