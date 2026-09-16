"""
Configurações de desenvolvimento.
Usa SQLite e sessões em banco de dados.
"""

from .base import *

DEBUG = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.db"

# Celery roda eager (síncrono inline) em dev, sem depender de Redis ativo.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_RESULT_BACKEND = "cache"
CELERY_CACHE_BACKEND = "memory"

# Em dev o canal é sempre o console: nada sai para o Telegram por acidente.
CANAL_PADRAO = "console"
TELEGRAM_ENABLED = False
