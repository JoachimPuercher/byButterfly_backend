"""The product itself: what every product has, whatever its group.

Product is the single entry point: everything else is reachable from here,
either as a column on this table (brand, author, the many-to-many lookups)
or through a related_name from the table that holds the foreign key
(translations, images, sources, specs, faqs, pros_cons, affiliate_links).
In a relational database the key always sits on the many side, so
product.images.all() reads a column on jenymia_productimage.

What only one product group has lives in product_groups.py: ToysProduct,
SchoolProduct and TechProduct inherit from Product (multi-table
inheritance), one per main category. A product is always one of them.
"""

from typing import ClassVar

from django.db import models

from apps.common.models import BaseModel

from .base import TranslationBase
from .choice_lists import AmpelScore, Country
from .lookups import Author, Badge, Brand, MainCategory, SubCategory

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

    # Traffic light verdict: 1 = red, 2 = yellow, 3 = green. A row, not a
    # number, because the verdict in words is shown too - in both languages.
    ampel_score = models.ForeignKey(
        AmpelScore,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="products",
    )

    # The manufacturer's own list price, read off the manufacturer page - not
    # a shop price, which changes daily and nobody here maintains. It is shown
    # next to the shop buttons as a reference and is deliberately kept out of
    # the structured data: a price in the search result answers the question
    # before the visitor ever sees the cheaper offers on the page.
    price_official = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    price_checked_at = models.DateField(null=True, blank=True)

    is_published = models.BooleanField(default=False)
    # Feeds schema.org datePublished and the sitemap; set once on publish and
    # never touched again, unlike updated_at.
    published_at = models.DateTimeField(null=True, blank=True)
    # "Last checked on" - the freshness signal AI search engines look for.
    last_verified_at = models.DateTimeField(null=True, blank=True)

    age_min_months = models.PositiveSmallIntegerField(null=True, blank=True)
    age_max_months = models.PositiveSmallIntegerField(null=True, blank=True)
    usage_lifespan_months = models.PositiveSmallIntegerField(null=True, blank=True)
    manufactured_in_country = models.ForeignKey(
        Country,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="products",
    )

    # The product group this belongs to: it decided which prompt analysed it,
    # it is the breadcrumb and the canonical home for search engines, and it
    # is what a listing filters on. Exactly one, out of three - and always
    # the one of the product's class (see save()).
    primary_category = models.ForeignKey(
        MainCategory, on_delete=models.PROTECT, related_name="products"
    )
    # Any number, flat. This is how a product also appears under another main
    # category without getting a second home.
    sub_categories = models.ManyToManyField(
        SubCategory, related_name="products", blank=True
    )
    badges = models.ManyToManyField(Badge, related_name="products", blank=True)

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
        # The range of the score and the format of the country code are
        # guarded by AmpelScore and Country themselves.
        constraints = [
            models.CheckConstraint(
                condition=models.Q(age_min_months__isnull=True)
                | models.Q(age_max_months__isnull=True)
                | models.Q(age_min_months__lte=models.F("age_max_months")),
                name="product_age_range_valid",
            ),
            # A published product without a verdict or without a publication
            # date would break the traffic light and the sitemap.
            models.CheckConstraint(
                condition=models.Q(is_published=False)
                | models.Q(ampel_score__isnull=False, published_at__isnull=False),
                name="product_published_requires_score_and_date",
            ),
        ]

    # The main category whose products a class holds. Set by every group
    # class in product_groups.py; the bare Product has none.
    MAIN_CATEGORY_SLUG: ClassVar[str] = ""

    def __str__(self) -> str:
        translation = self.translations.first()
        return translation.title if translation else str(self.pk)

    def save(self, *args, **kwargs) -> None:
        """A product is always one of the groups, and its main category is
        the group's. The database cannot see across the two tables, so this
        is where it is kept true: a bare Product is refused, an empty main
        category is taken from the class, a different one is refused.
        QuerySet.update() bypasses this and must never touch the category."""
        if not self.MAIN_CATEGORY_SLUG:
            raise TypeError(
                "Save a ToysProduct, SchoolProduct or TechProduct, not a bare Product."
            )
        if self.primary_category_id is None:
            self.primary_category = MainCategory.objects.get(
                slug=self.MAIN_CATEGORY_SLUG
            )
        elif self.primary_category.slug != self.MAIN_CATEGORY_SLUG:
            raise ValueError(
                f"A {type(self).__name__} belongs to {self.MAIN_CATEGORY_SLUG!r}, "
                f"not to {self.primary_category.slug!r}."
            )
        super().save(*args, **kwargs)


class ProductTranslation(TranslationBase):
    """One row per language, with the texts every product has. The slug
    lives here because the URL is /<locale>/<slug>/ and each language needs
    its own, plus hreflang between them. The texts of one group only (safety,
    privacy, growth) are in the group's translation table."""

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
