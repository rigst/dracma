"""Admin dos documentos legais.

O admin é a fonte da verdade para o texto das políticas, o que só é defensável
porque a imutabilidade é imposta aqui, e não pela disciplina de quem edita:
documento publicado não se altera nem se apaga, e aceite é somente leitura.
"""

import csv

from django.contrib import admin, messages
from django.http import HttpResponse
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin

from .models import AceiteLegal, DocumentoLegal, StatusDocumento
from .utils import proxima_versao


@admin.register(DocumentoLegal)
class DocumentoLegalAdmin(ModelAdmin):
    list_display = ("titulo", "tipo", "versao", "status", "material", "publicado_em", "qtd_aceites")
    list_filter = ("tipo", "status", "material")
    search_fields = ("titulo", "versao", "corpo_md")
    ordering = ("tipo", "-criado_em")
    readonly_fields = ("sha256", "publicado_em", "criado_em", "atualizado_em", "previa")
    actions = ("publicar_selecionados", "duplicar_como_nova_versao")
    fieldsets = (
        (None, {"fields": ("tipo", "versao", "titulo", "status", "material", "vigente_desde")}),
        ("Texto", {"fields": ("corpo_md", "previa")}),
        ("Registro", {"fields": ("sha256", "publicado_em", "criado_em", "atualizado_em")}),
    )

    @admin.display(description="aceites")
    def qtd_aceites(self, obj):
        return obj.aceites.count()

    @admin.display(description="Pré-visualização")
    def previa(self, obj):
        if not obj or not obj.pk:
            return "Salve o rascunho para ver a pré-visualização."
        # O HTML já passou pelo nh3; mark_safe (e não format_html) porque o texto
        # legal contém chaves que format_html interpretaria como placeholder.
        html = obj.corpo_html if obj.publicado else obj.html_preview()
        return mark_safe(
            '<div style="max-width:60rem;padding:1rem;border:1px solid #d4d4d8;'
            'border-radius:.5rem;background:#fff;color:#18181b">' + html + "</div>"
        )

    # -- Travas de imutabilidade ------------------------------------------
    # Publicado nunca é editável. Não basta "publicado sem aceite ainda":
    # entre o publicar e o primeiro aceite, o texto já está no ar, e alterá-lo
    # deixaria o site exibindo algo diferente do que foi congelado no sha256.

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.status != StatusDocumento.RASCUNHO:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and (obj.status != StatusDocumento.RASCUNHO or obj.tem_aceites):
            return False
        return super().has_delete_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        campos = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.status != StatusDocumento.RASCUNHO:
            campos += [
                "tipo",
                "versao",
                "titulo",
                "status",
                "material",
                "corpo_md",
                "vigente_desde",
            ]
        return tuple(dict.fromkeys(campos))

    # -- Publicação --------------------------------------------------------
    # Só por ação da changelist, nunca por link: publicar muda estado, e ação de
    # admin já vem como POST com CSRF e confirmação da seleção.

    @admin.action(description="Publicar rascunhos selecionados")
    def publicar_selecionados(self, request, queryset):
        publicados = 0
        for documento in queryset.filter(status=StatusDocumento.RASCUNHO):
            documento.publicar()
            publicados += 1
        if publicados:
            self.message_user(request, f"{publicados} documento(s) publicado(s).", messages.SUCCESS)
        else:
            self.message_user(request, "Nenhum rascunho na seleção.", messages.WARNING)

    @admin.action(description="Duplicar como nova versão (rascunho)")
    def duplicar_como_nova_versao(self, request, queryset):
        criados = 0
        for documento in queryset:
            nova = proxima_versao(documento.versao)
            while DocumentoLegal.objects.filter(tipo=documento.tipo, versao=nova).exists():
                nova = proxima_versao(nova)
            DocumentoLegal.objects.create(
                tipo=documento.tipo,
                versao=nova,
                titulo=documento.titulo,
                status=StatusDocumento.RASCUNHO,
                corpo_md=documento.corpo_md,
                material=True,
            )
            criados += 1
        self.message_user(
            request,
            f"{criados} rascunho(s) criado(s) a partir da seleção. Edite e publique quando estiver pronto.",
            messages.SUCCESS,
        )


@admin.register(AceiteLegal)
class AceiteLegalAdmin(ModelAdmin):
    """Somente leitura: este modelo é prova, não cadastro."""

    list_display = (
        "aceito_em",
        "usuario_label",
        "documento",
        "origem",
        "ip",
        "e_visitante",
        "integridade",
    )
    list_filter = ("origem", "e_visitante", "documento__tipo", "documento__versao", "aceito_em")
    search_fields = ("usuario_label", "ip", "session_key", "documento_sha256")
    date_hierarchy = "aceito_em"
    actions = ("exportar_csv",)

    @admin.display(description="íntegro", boolean=True)
    def integridade(self, obj):
        return obj.integro

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Exportar seleção em CSV")
    def exportar_csv(self, request, queryset):
        resposta = HttpResponse(content_type="text/csv; charset=utf-8")
        resposta["Content-Disposition"] = 'attachment; filename="aceites.csv"'
        escritor = csv.writer(resposta)
        escritor.writerow(
            [
                "aceito_em",
                "usuario",
                "e_visitante",
                "documento",
                "versao",
                "sha256_aceito",
                "sha256_atual",
                "integro",
                "origem",
                "ip",
                "session_key",
                "user_agent",
            ]
        )
        for aceite in queryset.select_related("documento"):
            escritor.writerow(
                [
                    aceite.aceito_em.isoformat(),
                    aceite.usuario_label,
                    "sim" if aceite.e_visitante else "não",
                    aceite.documento.get_tipo_display(),
                    aceite.documento.versao,
                    aceite.documento_sha256,
                    aceite.documento.sha256,
                    "sim" if aceite.integro else "NÃO",
                    aceite.get_origem_display(),
                    aceite.ip or "",
                    aceite.session_key,
                    aceite.user_agent,
                ]
            )
        return resposta
