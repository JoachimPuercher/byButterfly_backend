"""
Local development settings.

Loads variables from .env (gitignored) before importing base settings, so
secrets never live in code. Copy .env.example to .env to get started.
"""

from pathlib import Path

from dotenv import load_dotenv

# Variables already present in the process (e.g. from docker-compose env_file)
# take precedence; load_dotenv does not override them.
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from .base import *

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]


# Email: print to console instead of sending
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}
