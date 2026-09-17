from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from unfold.admin import ModelAdmin

from .models import ConsumoIA, ConviteEspaco, Espaco, Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin, ModelAdmin):
    list_display = ("username", "email", "espaco", "is_visitante", "ultimo_acesso", "is_active")
    list_filter = ("is_visitante", "is_active", "is_staff")
    fieldsets = (
        *(UserAdmin.fieldsets or ()),
        ("Dracma", {"fields": ("espaco", "is_visitante", "ultimo_acesso")}),
    )


@admin.register(Espaco)
class EspacoAdmin(ModelAdmin):
    list_display = ("nome", "criado_em")
    search_fields = ("nome",)


@admin.register(ConviteEspaco)
class ConviteEspacoAdmin(ModelAdmin):
    list_display = ("codigo", "espaco", "criado_por", "usado_em", "expira_em")
    readonly_fields = ("criado_em",)


@admin.register(ConsumoIA)
class ConsumoIAAdmin(ModelAdmin):
    """Só leitura: é registro contábil do que foi gasto na API."""

    list_display = ("usuario", "modelo", "total_tokens", "custo_usd", "criado_em")
    list_filter = ("modelo",)
    readonly_fields = [campo.name for campo in ConsumoIA._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
