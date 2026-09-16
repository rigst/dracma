from django.urls import path

from . import views

app_name = "carteira"

urlpatterns = [
    path("", views.painel, name="painel"),
    path("transacoes/", views.transacoes, name="transacoes"),
    path("lancamento/novo/", views.nova_transacao, name="nova_transacao"),
    path("lancamento/<str:codigo>/", views.editar_transacao, name="editar_transacao"),
    path("lancamento/<str:codigo>/apagar/", views.excluir_transacao, name="excluir_transacao"),
    path("lancamento/<str:codigo>/pago/", views.alternar_pago, name="alternar_pago"),
    path("limite/novo/", views.novo_limite, name="novo_limite"),
    path("limite/<int:pk>/apagar/", views.excluir_limite, name="excluir_limite"),
    path("recorrente/novo/", views.novo_recorrente, name="novo_recorrente"),
    path("recorrente/<int:pk>/apagar/", views.excluir_recorrente, name="excluir_recorrente"),
    path("conta/nova/", views.nova_conta, name="nova_conta"),
    path("compartilhar/", views.compartilhar, name="compartilhar"),
    path("compartilhar/sair/", views.sair_do_espaco, name="sair_do_espaco"),
    path("compartilhar/nome/", views.renomear_espaco, name="renomear_espaco"),
    path("compartilhar/divisao/", views.divisao_padrao, name="divisao_padrao"),
    path("acerto/", views.registrar_acerto, name="registrar_acerto"),
    path("acerto/<int:pk>/desfazer/", views.desfazer_acerto, name="desfazer_acerto"),
    path("exportar/", views.exportar, name="exportar"),
]
