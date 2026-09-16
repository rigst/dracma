"""Tasks do Celery do app accounts."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def cleanup_expired_visitors() -> int:
    """Apaga visitantes inativos e tudo que é deles.

    A política de privacidade promete isso. O `delete()` em cascata leva o
    espaço, as transações e as mensagens junto, é a promessa cumprida, não um
    efeito colateral.
    """
    from .models import Espaco, Usuario

    limite = timezone.now() - timedelta(hours=settings.VISITOR_EXPIRY_HOURS)
    expirados = Usuario.objects.filter(is_visitante=True, ultimo_acesso__lt=limite)

    espacos = list(expirados.values_list("espaco_id", flat=True))
    total = expirados.count()
    expirados.delete()

    # O espaço de um visitante existe só para ele: sem dono, vira órfão com os
    # lançamentos dentro.
    orfaos = Espaco.objects.filter(pk__in=[e for e in espacos if e], membros__isnull=True)
    apagados = orfaos.count()
    orfaos.delete()

    if total:
        logger.info("Visitantes expirados: %s (e %s espaço[s] órfão[s]).", total, apagados)
    return total
