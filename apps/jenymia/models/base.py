"""Shared building blocks for the jenymia models.

Everything the product tables have in common lives here: the locale set and
the abstract parent for every translation table. Concrete models add their
own foreign key and their own UniqueConstraint(<model>, locale).
"""

from django.db import models

from apps.common.models import BaseModel


class Locale(models.TextChoices):
    """Languages the site is published in. Adding one means adding
    translations for every translated table, so this list is deliberately
    short."""

    DE = "de", "Deutsch"
    EN = "en", "English"


class TranslationBase(BaseModel):
    """Abstract parent for all <Model>Translation tables."""

    locale = models.CharField(max_length=2, choices=Locale.choices)

    class Meta:
        abstract = True
