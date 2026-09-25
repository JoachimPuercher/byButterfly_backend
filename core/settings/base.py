"""
Shared settings for all environments.

Never used directly as DJANGO_SETTINGS_MODULE. Environment-specific modules
(dev.py, prod.py) import everything from here and override what differs.
Values that change between deployments come from the environment (12-factor).
"""

import os
from pathlib import Path

# core/settings/base.py -> core/settings -> core -> project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# No defaults anywhere in the settings: every value is set explicitly per
# environment. A missing variable raises KeyError at startup, on purpose.
SECRET_KEY = os.environ["SECRET_KEY"]

# Background workers (django-rq + Redis) are opt-in per environment.
# Phase 1: the Railway web service serves only the API and the admin and has
# no broker; the worker, Redis and the analysis pipeline run locally and talk
# to the production database directly through the ORM. Set the flag to True
# locally and to False on Railway until the worker moves there (backlog 5.6).
DEPLOY_BACKGROUND_WORKERS = os.environ["DEPLOY_BACKGROUND_WORKERS"].lower() in (
    "1",
    "true",
    "yes",
)


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.common",
    "apps.accounts",
    "apps.jenymia",
]

if DEPLOY_BACKGROUND_WORKERS:
    INSTALLED_APPS.append("django_rq")

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases
# Structure is identical everywhere; only the POSTGRES_* variables differ
# per environment (.env locally, docker-compose or platform variables otherwise).

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.environ["POSTGRES_PORT"],
        # "prefer" for the local Docker database and Railway's private network;
        # "require" when the local pipeline connects through the public TCP proxy.
        "OPTIONS": {"sslmode": os.environ["POSTGRES_SSLMODE"]},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Cache
# Holds the throttle counters. In-memory is enough while no frontend calls
# the API: it lives in the worker process, so each gunicorn worker counts on
# its own and the effective limit is multiplied. Before the frontend goes
# live this has to become a shared backend (Redis, or the database backend
# plus a one-off "manage.py createcachetable"), otherwise the limit below is
# a rough guideline rather than a limit.

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "bybutterfly",
    }
}


# Django REST Framework
# Authentication is session only: DRF's default also enables HTTP Basic,
# which would let anyone try staff passwords against every API endpoint,
# and authentication runs before throttling, so the rate limit would not
# stop it. Permissions are closed by default; each view opens itself
# explicitly (AllowAny on the public read endpoints).
# Throttle format is DRF's: "<number>/<second|minute|hour|day>".

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAdminUser",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "jenymia_product_detail_anon": "60/min",
        "jenymia_product_detail_user": "120/min",
    },
}


# Media (Cloudflare R2 in production, any static host locally)
# The database stores relative keys only; this is the domain they hang from.

JENYMIA_MEDIA_BASE_URL = os.environ["JENYMIA_MEDIA_BASE_URL"]


# Analysis pipeline
# JENYMIA_AUDIO_DIR holds downloaded audio between download and transcript
# only - the file is deleted once its text is stored, so the directory never
# needs to be persistent and works on an ephemeral container filesystem.

JENYMIA_AUDIO_DIR = os.environ["JENYMIA_AUDIO_DIR"]
MAX_VIDEO_DURATION_SECONDS = int(os.environ["MAX_VIDEO_DURATION_SECONDS"])
# Total executions of the ingest job including the first one; rq gets
# INGEST_MAX_ATTEMPTS - 1 retries and refuses a retry count of zero.
INGEST_MAX_ATTEMPTS = int(os.environ["INGEST_MAX_ATTEMPTS"])
if INGEST_MAX_ATTEMPTS < 2:
    raise ValueError("INGEST_MAX_ATTEMPTS must be at least 2.")

WHISPER_MODEL = os.environ["WHISPER_MODEL"]
WHISPER_DEVICE = os.environ["WHISPER_DEVICE"]
WHISPER_COMPUTE_TYPE = os.environ["WHISPER_COMPUTE_TYPE"]

# Language models for the extract job. Gemini answers on the free tier;
# Claude takes over once that quota is spent (pipeline_shared/
# select_public_LLM.py). The keys may be empty where no worker runs.
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.environ["GEMINI_MODEL"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_MODEL = os.environ["ANTHROPIC_MODEL"]


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"


# Task queue (django-rq)
# One queue is enough until a slow job type measurably blocks a fast one.
# Redis is the broker only; job state lives in Postgres.
# Only defined when DEPLOY_BACKGROUND_WORKERS is set (see top of file).

if DEPLOY_BACKGROUND_WORKERS:
    RQ_QUEUES = {
        "default": {
            "HOST": os.environ["REDIS_HOST"],
            "PORT": int(os.environ["REDIS_PORT"]),
            "DB": int(os.environ["REDIS_DB"]),
            # docker-compose starts Redis with --requirepass ${REDIS_PASSWORD}
            "PASSWORD": os.environ["REDIS_PASSWORD"],
            "DEFAULT_TIMEOUT": int(os.environ["RQ_DEFAULT_TIMEOUT"]),
            "DEFAULT_RESULT_TTL": int(os.environ["RQ_RESULT_TTL"]),
            "REDIS_CLIENT_KWARGS": {},
        },
    }
