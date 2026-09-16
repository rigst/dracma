"""Contexto compartilhado por todos os templates."""

from django.conf import settings

from .quota import tokens_restantes


def espaco_context(request):
    ctx = {
        "signup_enabled": getattr(settings, "SIGNUP_ENABLED", False),
        "telegram_habilitado": getattr(settings, "TELEGRAM_ENABLED", False),
    }
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return ctx

    ctx.update(
        {
            "espaco": user.espaco,
            "quota_total": user.quota_tokens,
            "quota_restante": tokens_restantes(user),
            # O aviso de "conecte seu Telegram" sai do base.html, então a
            # informação precisa estar em toda página, não só no painel.
            "telegram_conectado": user.contas_telegram.filter(verificado_em__isnull=False).exists(),
        }
    )
    return ctx
