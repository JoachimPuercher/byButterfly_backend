"""Fixed choice lists, each one a table with a label per language.

Every value the frontend shows as text comes from the database, in both
languages - one source for all wording, none of it translated in the
frontend. So a choice list is not a TextChoices in code but a table: the
slug (or the ISO code, or the score) is the stable machine value that the
pipeline writes and the API filters on, the translation row carries the
label a visitor reads.

The rows are seeded by migration 0002; the admin may correct a label or add
a value. The analysis only proposes values that exist here, because the
choices it gets are read from these tables (pipeline_shared/analysis/extract.py).
"""

from django.db import models

from apps.common.models import BaseModel

from .base import TranslationBase


class PrefetchTranslationsManager(models.Manager):
    """Every query brings the labels along, so a select box or a __str__
    costs one query for all rows instead of one per row."""

    def get_queryset(self) -> models.QuerySet:
        return super().get_queryset().prefetch_related("translations")


class LabelledMixin:
    """label_in() for every model whose translations carry a `label`."""

    def label_in(self, locale: str) -> str:
        """The label in the given language; empty if that translation is
        missing. Reads from .all() so a prefetch covers it."""
        return next(
            (t.label for t in self.translations.all() if t.locale == locale), ""
        )


class ChoiceList(LabelledMixin, BaseModel):
    """A list of values identified by a slug."""

    slug = models.SlugField(max_length=30, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    objects = PrefetchTranslationsManager()

    class Meta:
        abstract = True
        ordering = ("sort_order", "slug")

    def __str__(self) -> str:
        # English, like every label the admin shows.
        return self.label_in("en") or self.slug


class ChoiceLabel(TranslationBase):
    """The label of one list value in one language."""

    label = models.CharField(max_length=100)

    class Meta:
        abstract = True


class DataType(ChoiceList):
    """Tech pipeline: which kind of data a device or app collects."""


class DataTypeTranslation(ChoiceLabel):
    data_type = models.ForeignKey(
        DataType, on_delete=models.CASCADE, related_name="translations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["data_type", "locale"], name="data_type_one_label_per_locale"
            )
        ]


class ServerRegion(ChoiceList):
    """Tech pipeline: where collected data is stored. "unknown" is a value of
    its own - the manufacturer saying nothing is itself an answer."""


class ServerRegionTranslation(ChoiceLabel):
    server_region = models.ForeignKey(
        ServerRegion, on_delete=models.CASCADE, related_name="translations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["server_region", "locale"],
                name="server_region_one_label_per_locale",
            )
        ]


class Availability(ChoiceList):
    """Whether a shop has the product."""

    class Meta(ChoiceList.Meta):
        verbose_name_plural = "availabilities"


class AvailabilityTranslation(ChoiceLabel):
    availability = models.ForeignKey(
        Availability, on_delete=models.CASCADE, related_name="translations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["availability", "locale"],
                name="availability_one_label_per_locale",
            )
        ]


class SourceType(ChoiceList):
    """What kind of publication a source is. Chosen by hand when the URL is
    entered, never guessed by the analysis. "youtube" is set by the pipeline
    for video sources; web URLs choose from the others."""


class SourceTypeTranslation(ChoiceLabel):
    source_type = models.ForeignKey(
        SourceType, on_delete=models.CASCADE, related_name="translations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["source_type", "locale"],
                name="source_type_one_label_per_locale",
            )
        ]


class ProsConType(ChoiceList):
    """ "pro" or "con". The label is the heading of the list on the page."""


class ProsConTypeTranslation(ChoiceLabel):
    pros_con_type = models.ForeignKey(
        ProsConType, on_delete=models.CASCADE, related_name="translations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["pros_con_type", "locale"],
                name="pros_con_type_one_label_per_locale",
            )
        ]


class AmpelScore(LabelledMixin, BaseModel):
    """The traffic light verdict: 1 = red, 2 = yellow, 3 = green. The value
    is what the analysis writes and what a listing sorts by; the label is
    the verdict in words."""

    value = models.PositiveSmallIntegerField(unique=True)

    objects = PrefetchTranslationsManager()

    class Meta:
        ordering = ("value",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(value__gte=1, value__lte=3),
                name="ampel_score_between_1_and_3",
            )
        ]

    def __str__(self) -> str:
        return f"{self.value} - {self.label_in('en')}"


class AmpelScoreTranslation(ChoiceLabel):
    ampel_score = models.ForeignKey(
        AmpelScore, on_delete=models.CASCADE, related_name="translations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ampel_score", "locale"],
                name="ampel_score_one_label_per_locale",
            )
        ]


class Country(LabelledMixin, BaseModel):
    """Countries by ISO 3166-1 alpha-2 code, with their name per language.
    Seeded from pycountry, so every valid code the analysis writes exists."""

    code = models.CharField(max_length=2, unique=True)

    objects = PrefetchTranslationsManager()

    class Meta:
        ordering = ("code",)
        verbose_name_plural = "countries"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(code__regex=r"^[A-Z]{2}$"),
                name="country_code_is_iso_alpha2",
            )
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.label_in('en')}"


class CountryTranslation(ChoiceLabel):
    country = models.ForeignKey(
        Country, on_delete=models.CASCADE, related_name="translations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["country", "locale"], name="country_one_label_per_locale"
            )
        ]
