"""
Configurações de produção.
Usa PostgreSQL, Redis para cache/sessões, e configurações de segurança.
"""

import os
from pathlib import Path

from .base import *

DEBUG = False


def _required_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"A variável de ambiente {name} é obrigatória em produção.")
    return value


SECRET_KEY = _required_env("SECRET_KEY")
if SECRET_KEY.startswith("django-insecure-"):
    raise RuntimeError("SECRET_KEY de produção não pode usar valor django-insecure.")

ALLOWED_HOSTS = [h.strip() for h in _required_env("ALLOWED_HOSTS").split(",") if h.strip()]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _required_env("DB_NAME"),
        "USER": _required_env("DB_USER"),
        "PASSWORD": _required_env("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": 600,
    }
}

# Redis dividido por responsabilidade para que um flush/estouro de um domínio
# não derrube os demais: cache /4, sessões /5, broker Celery /6 (em base.py).
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.getenv("CACHE_REDIS_URL", "redis://localhost:6379/4"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    },
    "sessions": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.getenv("SESSION_REDIS_URL", "redis://localhost:6379/5"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    },
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "sessions"

# Segurança
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

STATIC_ROOT = Path(os.getenv("STATIC_ROOT") or BASE_DIR / "staticfiles")

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
    },
}

CSRF_TRUSTED_ORIGINS = ["https://" + h for h in ALLOWED_HOSTS if h]


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "padrao": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "padrao"}},
    "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}

# ==============================================================================
# Monitoramento de erros (Sentry), ativo só quando SENTRY_DSN está definido.
# ==============================================================================

SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if SENTRY_DSN:
    try:
        import sentry_sdk
        from django.core.exceptions import DisallowedHost
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration(), CeleryIntegration()],
            environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
            release=os.getenv("SENTRY_RELEASE") or None,
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            # O corpo das mensagens do Telegram é dado financeiro do usuário e
            # não pode vazar para o Sentry junto com o stacktrace.
            send_default_pii=False,
            ignore_errors=[DisallowedHost],
        )
    except Exception:
        # Pacote ausente ou integração indisponível: seguimos sem monitoramento.
        pass
