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
DEPLOY_BACKGROUND_WORKERS = os.environ["DEPLOY_BACKGROUND_WORKERS"].lower() in ("1", "true", "yes")


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
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


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
