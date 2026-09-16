from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import CodigoPareamento, ContaTelegram, Mensagem, Midia


@admin.register(ContaTelegram)
class ContaTelegramAdmin(ModelAdmin):
    list_display = ("chat_id", "username", "usuario", "verificado_em", "bloqueado_em", "criado_em")
    list_filter = ("verificado_em", "bloqueado_em")
    search_fields = ("chat_id", "username", "primeiro_nome")


@admin.register(Mensagem)
class MensagemAdmin(ModelAdmin):
    """Só leitura: é o log do que entrou e saiu, e editar destruiria a trilha."""

    list_display = ("criada_em", "direcao", "tipo", "canal", "status", "resumo")
    list_filter = ("direcao", "tipo", "canal", "status")
    search_fields = ("texto", "transcricao", "id_externo")
    list_select_related = ("conta", "usuario")
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


@admin.register(CodigoPareamento)
class CodigoPareamentoAdmin(ModelAdmin):
    list_display = ("codigo", "usuario", "criado_em", "usado_em", "expira_em")
