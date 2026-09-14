from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import CodigoPareamento, JanelaAtendimento, Mensagem, Midia, NumeroWhatsApp


@admin.register(NumeroWhatsApp)
class NumeroWhatsAppAdmin(ModelAdmin):
    list_display = ("numero", "usuario", "verificado_em", "criado_em")
    search_fields = ("numero",)


@admin.register(Mensagem)
class MensagemAdmin(ModelAdmin):
    """Só leitura: é o log do que entrou e saiu, e editar destruiria a trilha."""

    list_display = ("criada_em", "direcao", "tipo", "canal", "status", "resumo")
    list_filter = ("direcao", "tipo", "canal", "status")
    search_fields = ("texto", "transcricao", "wamid")
    list_select_related = ("numero", "usuario")
    readonly_fields = [campo.name for campo in Mensagem._meta.fields]

    @admin.display(description="conteúdo")
    def resumo(self, obj):
        return (obj.conteudo or "")[:60]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Midia)
class MidiaAdmin(ModelAdmin):
    list_display = ("mensagem", "mime_type", "tamanho", "criada_em")
    list_filter = ("mime_type",)


@admin.register(JanelaAtendimento)
class JanelaAtendimentoAdmin(ModelAdmin):
    list_display = ("numero", "ultimo_inbound_em", "aberta")

    @admin.display(boolean=True, description="aberta")
    def aberta(self, obj):
        return obj.aberta


@admin.register(CodigoPareamento)
class CodigoPareamentoAdmin(ModelAdmin):
    list_display = ("codigo", "usuario", "criado_em", "usado_em", "expira_em")
