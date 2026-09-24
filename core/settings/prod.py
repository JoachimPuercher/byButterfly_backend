"""
Production settings.

Does not read a .env file: every value comes from the process environment
(Railway variables, Docker ENV, ...). Missing variables fail at startup.
Verify with: DJANGO_SETTINGS_MODULE=core.settings.prod manage.py check --deploy
"""

import os

from .base import *  # noqa: F403

DEBUG = False

# Comma-separated, e.g. "api.example.com,example.com"
ALLOWED_HOSTS = [h.strip() for h in os.environ["ALLOWED_HOSTS"].split(",") if h.strip()]


# HTTPS / cookies
# https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# Behind a reverse proxy that terminates TLS (Railway). The proxy must
# overwrite this header; otherwise clients could spoof it.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# Logging: plain lines to stdout, collected by the platform

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ["LOG_LEVEL"],
    },
}
