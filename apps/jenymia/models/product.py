"""The product itself.

Product is the single entry point: everything else is reachable from here,
either as a column on this table (brand, author, the many-to-many lookups)
or through a related_name from the table that holds the foreign key
(translations, images, sources, specs, faqs, pros_cons, affiliate_links,
data_categories). In a relational database the key always sits on the many
side, so product.images.all() reads a column on jenymia_productimage.
"""

from django.db import models

from apps.common.models import BaseModel

from .base import TranslationBase
from .lookups import Author, Badge, Brand, Category, LearningBadge

# Prices are stored as a plain amount. The currency is fixed for the whole
# site and is emitted by the serializer, so it needs no column.
PRICE_CURRENCY = "EUR"


class Product(BaseModel):
    brand = models.ForeignKey(
        Brand, on_delete=models.PROTECT, null=True, blank=True, related_name="products"
    )
    # Manufacturer model designation, e.g. "iPhone 14" or "10913". Part of the
    # slug, because brand + title + model identifies a product on the market.
    model_name = models.CharField(max_length=120, blank=True)
    # Global Trade Item Number (EAN/UPC) for schema.org Product identity.
    gtin = models.CharField(max_length=14, blank=True)

    author = models.ForeignKey(
        Author, on_delete=models.PROTECT, null=True, blank=True, related_name="products"
    )

    # Traffic light verdict: 1 = red, 2 = yellow, 3 = green.
    ampel_score = models.PositiveSmallIntegerField(null=True, blank=True)

    price_current = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    price_original = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    is_published = models.BooleanField(default=False)
    # Feeds schema.org datePublished and the sitemap; set once on publish and
    # never touched again, unlike updated_at.
    published_at = models.DateTimeField(null=True, blank=True)
    # "Last checked on" - the freshness signal AI search engines look for.
    last_verified_at = models.DateTimeField(null=True, blank=True)

    age_min_months = models.PositiveSmallIntegerField(null=True, blank=True)
    age_max_months = models.PositiveSmallIntegerField(null=True, blank=True)
    usage_lifespan_months = models.PositiveSmallIntegerField(null=True, blank=True)
    # ISO 3166-1 alpha-2, e.g. "DE".
    manufactured_in_country = models.CharField(max_length=2, blank=True)

    is_offline_capable = models.BooleanField(default=False)
    requires_account = models.BooleanField(default=False)
    is_child_certified = models.BooleanField(default=False)

    categories = models.ManyToManyField(Category, related_name="products", blank=True)
    badges = models.ManyToManyField(Badge, related_name="products", blank=True)
    learning_badges = models.ManyToManyField(
        LearningBadge, related_name="products", blank=True
    )

    # The analysis run this product came out of. Kept so every published
    # field can be traced back to the sources it was derived from.
    analysis = models.OneToOneField(
        "jenymia.ProductToAnalyse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="product",
    )

    class Meta:
        ordering = ("-published_at",)
        indexes = [models.Index(fields=["is_published", "-published_at"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ampel_score__isnull=True)
                | models.Q(ampel_score__gte=1, ampel_score__lte=3),
                name="product_ampel_score_between_1_and_3",
            ),
            models.CheckConstraint(
                condition=models.Q(age_min_months__isnull=True)
                | models.Q(age_max_months__isnull=True)
                | models.Q(age_min_months__lte=models.F("age_max_months")),
                name="product_age_range_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(manufactured_in_country="")
                | models.Q(manufactured_in_country__regex=r"^[A-Z]{2}$"),
                name="product_country_is_iso_alpha2",
            ),
            # A published product without a verdict or without a publication
            # date would break the traffic light and the sitemap.
            models.CheckConstraint(
                condition=models.Q(is_published=False)
                | models.Q(ampel_score__isnull=False, published_at__isnull=False),
                name="product_published_requires_score_and_date",
            ),
        ]

    def __str__(self) -> str:
        translation = self.translations.first()
        return translation.title if translation else str(self.pk)


class ProductTranslation(TranslationBase):
    """One row per language. The slug lives here because the URL is
    /<locale>/<slug>/ and each language needs its own, plus hreflang between
    them."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="translations"
    )
    slug = models.SlugField(max_length=160)

    title = models.CharField(max_length=200)
    hook = models.CharField(max_length=300)
    description_short = models.TextField()
    description_detail = models.TextField()

    # SEO
    meta_title = models.CharField(max_length=70, blank=True)
    meta_description = models.CharField(max_length=180, blank=True)

    # GEO: the blocks AI search engines quote. summary is a self-contained
    # answer of 40-60 words, verdict a single quotable sentence,
    # question_headline the H1 phrased as a question.
    summary = models.TextField(blank=True)
    verdict = models.CharField(max_length=300, blank=True)
    question_headline = models.CharField(max_length=200, blank=True)

    # Group specific: toys and everyday products
    safety_short = models.TextField(blank=True)
    safety_long = models.TextField(blank=True)
    growth_info = models.TextField(blank=True)
    # Group specific: tech
    privacy_short = models.TextField(blank=True)
    privacy_long = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "locale"], name="product_one_translation_per_locale"
            ),
            models.UniqueConstraint(
                fields=["locale", "slug"], name="product_slug_unique_per_locale"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} [{self.locale}]"
