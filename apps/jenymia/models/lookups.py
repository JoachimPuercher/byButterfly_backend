"""Master data: brands, categories, badges, authors.

These tables are small, change rarely and are referenced by products.
They use PROTECT on delete: removing a brand that products point at must
fail loudly instead of silently deleting the products.
"""

from django.core.exceptions import ValidationError
from django.db import models

from apps.common.models import BaseModel

from .base import TranslationBase


class Pipeline(models.TextChoices):
    """Analysis pipelines. The value is also the file name of the prompt
    fragment in pipeline_shared/prompts/, e.g. "toys" -> prompts/toys.md."""

    TOYS = "toys", "Spielen & Lernen"
    SCHOOL = "school", "Schule & Alltag"
    TECH = "tech", "Tech & Sicherheit"


class Brand(BaseModel):
    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=100)
    # Relative key inside the media bucket, never an absolute URL: the domain
    # belongs to the environment (JENYMIA_MEDIA_BASE_URL), not to the data.
    logo_key = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class Category(BaseModel):
    """Two levels: a top level that carries the pipeline, and sub-categories
    that inherit it from their parent."""

    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
    )
    # Only set on top-level categories; blank on sub-categories.
    pipeline = models.CharField(max_length=10, choices=Pipeline.choices, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        translation = self.translations.first()
        return translation.name if translation else str(self.pk)

    def resolve_pipeline(self) -> str:
        """The pipeline of this category, or the one of its top-level parent.

        Raises instead of guessing: an analysis run with the wrong prompt is
        worse than one that stops."""
        pipeline = self.pipeline or (self.parent.pipeline if self.parent_id else "")
        if not pipeline:
            raise ValidationError(f"No pipeline set for category {self.pk}.")
        return pipeline


class CategoryTranslation(TranslationBase):
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="translations"
    )
    # Category pages live under /<locale>/<slug>/, so the slug is per language.
    slug = models.SlugField(max_length=120)
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["category", "locale"],
                name="category_one_translation_per_locale",
            ),
            models.UniqueConstraint(
                fields=["locale", "slug"], name="category_slug_unique_per_locale"
            ),
        ]


class Badge(BaseModel):
    """Property badges and test marks (CE, GS, FSC, ...). Kept as a table so
    a new mark is an admin entry, not a migration."""

    slug = models.SlugField(max_length=60, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)

    def __str__(self) -> str:
        return self.slug


class BadgeTranslation(TranslationBase):
    badge = models.ForeignKey(
        Badge, on_delete=models.CASCADE, related_name="translations"
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["badge", "locale"], name="badge_one_translation_per_locale"
            )
        ]


class LearningBadge(BaseModel):
    """Foerderbereiche: coordination, logic, creativity, language,
    fine-motor, social-emotional, concentration."""

    slug = models.SlugField(max_length=60, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)

    def __str__(self) -> str:
        return self.slug


class LearningBadgeTranslation(TranslationBase):
    learning_badge = models.ForeignKey(
        LearningBadge, on_delete=models.CASCADE, related_name="translations"
    )
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["learning_badge", "locale"],
                name="learning_badge_one_translation_per_locale",
            )
        ]


class UsageContext(BaseModel):
    """Where a product is used: school, kindergarten, leisure, on the go, at
    home. A second list next to the category tree, because "where" is a
    different question from "what": a drinking bottle is one product type
    used in four places. Filled by the analysis like badges, corrected in
    the admin before publication."""

    slug = models.SlugField(max_length=60, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)

    def __str__(self) -> str:
        return self.slug


class UsageContextTranslation(TranslationBase):
    usage_context = models.ForeignKey(
        UsageContext, on_delete=models.CASCADE, related_name="translations"
    )
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["usage_context", "locale"],
                name="usage_context_one_translation_per_locale",
            )
        ]


class Author(BaseModel):
    """The person who signs the published analysis - the E-E-A-T author for
    schema.org/Person. Not the operators of the analysed sources: those are
    credited as sources on the product, not as authors.

    Deliberately separate from accounts.User, which stays minimal and knows
    nothing about any product."""

    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=120, blank=True)
    bio = models.TextField(blank=True)
    credentials = models.TextField(blank=True)
    photo_key = models.CharField(max_length=300, blank=True)
    # Profile URLs for schema.org sameAs, e.g. ["https://linkedin.com/in/..."]
    same_as = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name
