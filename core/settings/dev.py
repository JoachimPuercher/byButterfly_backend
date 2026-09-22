"""
Local development settings.

Loads variables from .env (gitignored) before importing base settings, so
secrets never live in code. Copy .env.example to .env to get started.
"""

from pathlib import Path

import environ

environ.Env.read_env(Path(__file__).resolve().parent.parent.parent / ".env")

from .base import *  # noqa: E402, F403

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]


# Email: print to console instead of sending
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}
