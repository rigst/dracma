"""
Configuração do Celery para o projeto Dracma.
"""

import os

from celery import Celery

# Em produção o worker deve ser seguro mesmo se o EnvironmentFile não definir
# DJANGO_SETTINGS_MODULE — sem este default o beat migraria/consultaria o
# SQLite de dev reportando sucesso. Para desenvolvimento, exporte
# config.settings.development.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("config")

app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
