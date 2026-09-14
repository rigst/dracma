"""
Configurações base do Django — Dracma, assistente financeira no WhatsApp.
Compartilhadas entre development e production.
"""

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from celery.schedules import crontab
from dotenv import load_dotenv

# Carrega variáveis de ambiente do .env
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-dev-key-change-in-production")

DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()
]


# Application definition

INSTALLED_APPS = [
    # O unfold precisa vir antes do admin: é assim que os templates dele
    # sobrescrevem os do django.contrib.admin.
    "unfold",
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Apps do projeto
    "accounts",
    "core",
    "carteira",
    "zap",
    "ai",
    "legal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Depois do Authentication (precisa de request.user) e antes do
    # VisitorExpiryMiddleware: nova versão dos termos bloqueia o uso até ser
    # aceita, inclusive para visitantes.
    "legal.middleware.AceiteObrigatorioMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # Depois do MessageMiddleware: ao expirar a sessão do visitante ele grava um
    # aviso com messages, e antes daqui request._messages ainda não existe.
    "accounts.middleware.VisitorExpiryMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.espaco_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# O modelo de usuário é trocado desde a primeira migration: depois do primeiro
# migrate a troca exige cirurgia no banco.
AUTH_USER_MODEL = "accounts.Usuario"


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

# Com pt-br, o `floatformat` já troca o ponto decimal pela vírgula, mas o
# separador de milhar só aparece com isto ligado. Sem ele a saída é "1800,00" e
# a tentação é somar o `intcomma` do humanize — que usa a convenção INGLESA e
# produz "1,800,00". Ligado, `{{ v|floatformat:2 }}` sozinho dá "1.800,00".
USE_THOUSAND_SEPARATOR = True


# Static files

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# A suíte roda com `config.settings.development`, que herda o MEDIA_ROOT daqui
# — e BASE_DIR é a própria árvore de produção. Esta guarda é a rede: quem
# esquecer o override_settings cai no tempdir em vez da mídia do servidor.
# A terceira condição cobre `python -m pytest`, onde o argv[0] é o
# `__main__.py` do pacote e o prefixo não bate.
IS_TEST = (
    "test" in sys.argv
    or Path(sys.argv[0]).name.startswith(("pytest", "py.test"))
    or "pytest" in sys.modules
)

if IS_TEST:
    MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="dracma-test-media-"))


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Auth redirects
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "carteira:painel"
LOGOUT_REDIRECT_URL = "login"

SIGNUP_ENABLED = os.getenv("SIGNUP_ENABLED", "False").lower() in ("true", "1", "yes")
PASSWORD_RESET_TIMEOUT = int(os.getenv("PASSWORD_RESET_TIMEOUT", str(3 * 24 * 3600)))

# Expiração do visitante (horas de inatividade). O modo visitante é o que abre
# a demo pública do portfólio: qualquer pessoa usa o console do assistente sem
# estar na allowlist de 5 números do número de teste da Meta.
VISITOR_EXPIRY_HOURS = int(os.getenv("VISITOR_EXPIRY_HOURS", "48"))


# ==============================================================================
# Celery
# ==============================================================================

# Redis dividido por responsabilidade, com DBs EXCLUSIVOS deste app:
# 4=cache, 5=sessões, 6=broker/result do Celery.
#
# A alocação foi conferida contra o servidor em 14/09/2026 (`redis-cli INFO
# keyspace`): 0,1,2,3,9,10 já tinham chaves. Não reaproveitar um DB de outro
# app — um flush de cache lá derruba a fila daqui, e o estouro é silencioso.
CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/6")
)
CELERY_RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND", os.getenv("REDIS_URL", "redis://localhost:6379/6")
)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True

CELERY_BEAT_SCHEDULE = {
    "cleanup-expired-visitors": {
        "task": "accounts.tasks.cleanup_expired_visitors",
        "schedule": int(os.getenv("CLEANUP_INTERVAL_MINUTES", "60")) * 60,
    },
    # Orçamento: consumo por categoria, avisa em 80% e em 100%.
    "verificar-limites": {
        "task": "carteira.tasks.verificar_limites",
        "schedule": crontab(minute=5),
    },
    # Planejamento: materializa as previstas do mês antes de qualquer consulta.
    "projetar-recorrentes": {
        "task": "carteira.tasks.projetar_recorrentes",
        "schedule": crontab(hour=0, minute=10),
    },
    # Lembrete de vencimento: hoje e amanhã.
    "lembrar-vencimentos": {
        "task": "carteira.tasks.lembrar_vencimentos",
        "schedule": crontab(hour=int(os.getenv("HORA_LEMBRETE", "9")), minute=0),
    },
    "resumo-semanal": {
        "task": "carteira.tasks.resumo_semanal",
        "schedule": crontab(day_of_week=1, hour=9, minute=30),
    },
}

# A transcrição é pesada (ctranslate2 em CPU) e vai para uma fila própria,
# consumida por um worker dedicado (deploy/systemd/dracma_celery_midia.service),
# para não travar o atendimento das mensagens de texto na fila default.
CELERY_TASK_DEFAULT_QUEUE = "celery"
CELERY_TASK_ROUTES = {
    "zap.tasks.transcrever_audio": {"queue": "midia"},
}


# ==============================================================================
# E-mail
# ==============================================================================

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
# Porta 587 → STARTTLS (EMAIL_USE_TLS). Porta 465 → SSL implícito (EMAIL_USE_SSL).
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "False").lower() in ("true", "1", "yes")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Dracma <no-reply@dracma.stolben.com>")
SITE_URL = os.getenv("SITE_URL", "https://dracma.stolben.com")


# ==============================================================================
# IA (Anthropic / Claude)
# ==============================================================================

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Um modelo só. O agente do WhatsApp é curto (1-2 iterações de tool use) e a
# alavanca de custo aqui é o prompt caching do prefixo estável + o `effort`,
# não a troca de modelo.
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-5")

# `effort` é a primeira alavanca de custo dentro do mesmo modelo. Registrar um
# gasto é trabalho mecânico e roda em `low`; pergunta analítica ("dá pra
# comprar?", "onde posso cortar?") precisa de julgamento e sobe para `high`.
AI_EFFORT_REGISTRO = os.getenv("AI_EFFORT_REGISTRO", "low")
AI_EFFORT_ANALISE = os.getenv("AI_EFFORT_ANALISE", "high")

AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "4000"))

# Quantos turnos anteriores da conversa acompanham a mensagem. Teto do custo
# de contexto por chamada.
AI_HISTORICO_TURNOS = int(os.getenv("AI_HISTORICO_TURNOS", "10"))

# Teto de iterações do loop de tool use. O agente daqui resolve em 1-2; o teto
# existe para uma tool que erre em looping não queimar a quota do usuário.
AI_MAX_ITERACOES = int(os.getenv("AI_MAX_ITERACOES", "5"))

# Preços por 1M tokens (input, output) em USD, para a contabilidade de quota.
AI_PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
# Fallback para um modelo fora do dicionário acima. Alinhado ao Sonnet 5, que
# é o padrão — se ficasse no preço do Opus, a quota de um modelo desconhecido
# seria consumida quase três vezes mais rápido do que o real.
AI_PRICE_INPUT_PER_MTOK = float(os.getenv("AI_PRICE_INPUT_PER_MTOK", "2.0"))
AI_PRICE_OUTPUT_PER_MTOK = float(os.getenv("AI_PRICE_OUTPUT_PER_MTOK", "10.0"))

# Quotas mensais (tokens). O visitante é anônimo e a demo é pública: sem um
# teto próprio, uma visita insistente queima a conta da API.
QUOTA_TOKENS_DEFAULT = int(os.getenv("QUOTA_TOKENS_DEFAULT", "2000000"))
QUOTA_TOKENS_VISITOR = int(os.getenv("QUOTA_TOKENS_VISITOR", "100000"))

# Rajada por número/sessão, em contador de cache.
AI_LIMITE_MENSAGENS = int(os.getenv("AI_LIMITE_MENSAGENS", "30"))
AI_JANELA_S = int(os.getenv("AI_JANELA_S", "600"))

# Teto do texto livre que entra no agente.
AI_MAX_CHARS_MENSAGEM = int(os.getenv("AI_MAX_CHARS_MENSAGEM", "2000"))


# ==============================================================================
# Transcrição de áudio (faster-whisper, local)
# ==============================================================================

# A API da Anthropic não aceita áudio: os áudios do WhatsApp passam por aqui
# antes de virar texto para o agente.
#
# O modelo é baixado uma vez e fica FORA da árvore do projeto — se ficasse em
# BASE_DIR, cada deploy limpo rebaixaria ~500 MB.
WHISPER_MODELO = os.getenv("WHISPER_MODELO", "small")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_CACHE_DIR = os.getenv("WHISPER_CACHE_DIR", "/var/lib/dracma/whisper")
WHISPER_IDIOMA = os.getenv("WHISPER_IDIOMA", "pt")
# Áudio mais longo que isto é recusado com uma mensagem, em vez de ocupar o
# worker da fila `midia` por minutos.
WHISPER_MAX_SEGUNDOS = int(os.getenv("WHISPER_MAX_SEGUNDOS", "180"))
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")


# ==============================================================================
# WhatsApp Cloud API (Meta)
# ==============================================================================

# Desligar aqui faz o webhook responder 404 e o envio virar no-op: é como se
# sobe o código antes do número estar aprovado, sem quebrar nada.
WHATSAPP_ENABLED = os.getenv("WHATSAPP_ENABLED", "False").lower() in ("true", "1", "yes")

WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v23.0")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
# O número em si, em E.164 sem o "+", para montar o link wa.me e o QR da tela
# de conexão. É diferente do PHONE_NUMBER_ID, que é o identificador interno da
# Meta e não serve para discar.
WHATSAPP_NUMERO = os.getenv("WHATSAPP_NUMERO", "")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
# Segredo escolhido por nós e repetido no painel da Meta; é o que ela devolve
# no handshake GET do webhook.
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
# App Secret: assina cada POST em X-Hub-Signature-256. Sem ele qualquer um que
# descubra a URL injeta transação na conta alheia.
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")

# Janela de atendimento da Meta: dentro de 24h desde a ÚLTIMA mensagem do
# usuário, a resposta pode ser livre; fora disso, só template aprovado.
# É regra da plataforma, não preferência nossa — ver zap/janela.py.
WHATSAPP_JANELA_HORAS = int(os.getenv("WHATSAPP_JANELA_HORAS", "24"))

# Nomes dos templates utility aprovados no painel, usados quando a janela está
# fechada. Vazio = o alerta é adiado em vez de enviado.
WHATSAPP_TEMPLATE_LIMITE = os.getenv("WHATSAPP_TEMPLATE_LIMITE", "")
WHATSAPP_TEMPLATE_VENCIMENTO = os.getenv("WHATSAPP_TEMPLATE_VENCIMENTO", "")
WHATSAPP_TEMPLATE_IDIOMA = os.getenv("WHATSAPP_TEMPLATE_IDIOMA", "pt_BR")

# Teto do download de mídia da Graph API (bytes). O limite da própria Meta é
# 16 MB para áudio/imagem e 100 MB para documento; cortamos antes disso.
WHATSAPP_MAX_MIDIA_BYTES = int(os.getenv("WHATSAPP_MAX_MIDIA_BYTES", str(16 * 1024 * 1024)))

# Canal usado pelo app. `cloud_api` fala com a Meta; `console` grava no banco e
# desenha no portal (é o que sustenta a demo pública e o desenvolvimento local
# sem ngrok).
CANAL_PADRAO = os.getenv("CANAL_PADRAO", "cloud_api" if WHATSAPP_ENABLED else "console")


# ==============================================================================
# Regras do domínio
# ==============================================================================

# Percentual do limite em que o primeiro aviso dispara (o segundo é no estouro).
LIMITE_ALERTA_PERCENTUAL = int(os.getenv("LIMITE_ALERTA_PERCENTUAL", "80"))
# Moeda padrão dos lançamentos.
MOEDA_PADRAO = os.getenv("MOEDA_PADRAO", "BRL")


UNFOLD = {
    "SITE_TITLE": "Dracma",
    "SITE_HEADER": "Dracma",
    "SITE_SUBHEADER": "Administração",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "COLORS": {
        "primary": {
            "50": "245 243 255",
            "100": "237 233 254",
            "200": "221 214 254",
            "300": "196 181 253",
            "400": "167 139 250",
            "500": "139 92 246",
            "600": "124 58 237",
            "700": "109 40 217",
            "800": "91 33 182",
            "900": "76 29 149",
            "950": "46 16 101",
        },
    },
}

# Destino após o aceite nas telas do app `legal`.
LEGAL_REDIRECT_URL = "carteira:painel"
LEGAL_VISITOR_ACTION = "accounts:entrar_visitante"
LEGAL_VISITOR_EXTRA: dict[str, Any] = {}
# O webhook da Meta não pode ser interceptado pelo middleware de re-aceite: um
# 302 ali vira falha de entrega e, repetida, a Meta desabilita a subscrição.
LEGAL_ALLOWLIST_EXTRA = ("/zap/webhook/",)
