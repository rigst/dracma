"""
Configurações da suíte no CI: as de desenvolvimento, com PostgreSQL no lugar
do SQLite.

Produção roda em PostgreSQL, e o SQLite deixa passar o que lá quebra:
comparação de texto sensível a acento/caixa, `select_for_update`, tipos
estritos em agregação de Decimal, ordenação sem `order_by`. O CI central
(rigst/ci) sobe um postgres:16 com `postgres: true`, e o ci.yml aponta
`test-settings-module` para cá, com as credenciais dele no `test-env`.

Localmente o pytest.ini segue em `config.settings.development` (SQLite), para
a suíte rodar sem Postgres na máquina. Para rodar contra Postgres aqui:
`DJANGO_SETTINGS_MODULE=config.settings.test DB_NAME=... pytest`.
"""

import os

from .development import *

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "postgres"),
        "USER": os.getenv("DB_USER", "postgres"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}
