from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Alerta, Categoria, Conta, Limite, Recorrente, Transacao


@admin.register(Conta)
class ContaAdmin(ModelAdmin):
    list_display = ("nome", "espaco", "tipo", "saldo_inicial", "ativa")
    list_filter = ("tipo", "ativa")
    search_fields = ("nome",)


@admin.register(Categoria)
class CategoriaAdmin(ModelAdmin):
    list_display = ("nome", "emoji", "espaco", "tipo", "fixa", "ativa")
    list_filter = ("tipo", "fixa", "ativa")
    search_fields = ("nome",)


@admin.register(Transacao)
class TransacaoAdmin(ModelAdmin):
    list_display = ("codigo", "data", "descricao", "tipo", "valor", "categoria", "origem", "pago")
    list_filter = ("tipo", "origem", "pago", "prevista", "data")
    search_fields = ("codigo", "descricao")
    date_hierarchy = "data"
    # A lista carrega dezenas de linhas; sem isto é uma consulta por linha
    # para cada FK exibida.
    list_select_related = ("categoria", "conta", "espaco")
    readonly_fields = ("codigo", "criada_em", "atualizada_em")


@admin.register(Recorrente)
class RecorrenteAdmin(ModelAdmin):
    list_display = ("descricao", "espaco", "tipo", "valor", "dia_do_mes", "ativo")
    list_filter = ("tipo", "ativo")


@admin.register(Limite)
class LimiteAdmin(ModelAdmin):
    list_display = ("__str__", "espaco", "valor", "inicio", "fim", "ativo")
    list_filter = ("ativo",)


@admin.register(Alerta)
class AlertaAdmin(ModelAdmin):
    list_display = ("tipo", "chave", "referencia", "espaco", "adiado", "enviado_em")
    list_filter = ("tipo", "adiado")
    readonly_fields = ("enviado_em",)
