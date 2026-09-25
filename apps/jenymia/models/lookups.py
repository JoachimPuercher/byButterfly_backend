"""Master data: brands, sub-categories, badges, authors.

These tables are small, change rarely and are referenced by products.
They use PROTECT on delete: removing a brand that products point at must
fail loudly instead of silently deleting the products.

The three main categories are deliberately NOT a table: they are strategy,
fixed in code, and each one names the prompt file that analyses it. Their
display names live in the frontend, because three values that change only
with a deploy do not need a translation row each.
"""

from django.db import models

from apps.common.models import BaseModel

from .base import TranslationBase


class MainCategory(models.TextChoices):
    """The three product groups. One per product, and the one that decides
    which prompt analyses it: the value is the file name in
    pipeline_shared/prompts/, e.g. "toys_learning" -> toys_learning.md.

    The labels are German because the admin is German; what a visitor sees
    is translated in the frontend from the value."""

    TOYS_LEARNING = "toys_learning", "Spielen & Lernen"
    SCHOOL_EVERYDAY = "school_everyday", "Schule & Alltag"
    TECH_SAFETY = "tech_safety", "Tech & Sicherheit"


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


class SubCategory(BaseModel):
    """A flat list, not a tree. A product keeps one main category as its home
    and carries any number of sub-categories, and those are what make it show
    up elsewhere: a drinking bottle is "trinkflasche", "freizeit" and
    "schule" at once. A parent would force each of them under exactly one
    main category and the multiple visibility would be gone.

    Proposed by the analysis, corrected by hand before a product goes public.
    """

    # Empty means: use the prompt of the product's main category. Set it to
    # a file name in pipeline_shared/prompts/ when a group of products needs
    # its own wording. The answer keeps the shape of the main category.
    prompt_name = models.CharField(max_length=60, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)
        verbose_name_plural = "sub-categories"

    def __str__(self) -> str:
        translation = self.translations.first()
        return translation.name if translation else str(self.pk)


class SubCategoryTranslation(TranslationBase):
    sub_category = models.ForeignKey(
        SubCategory, on_delete=models.CASCADE, related_name="translations"
    )
    # Sub-category pages live under /<locale>/<slug>/, so the slug is per
    # language.
    slug = models.SlugField(max_length=120)
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["sub_category", "locale"],
                name="sub_category_one_translation_per_locale",
            ),
            models.UniqueConstraint(
                fields=["locale", "slug"], name="sub_category_slug_unique_per_locale"
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
