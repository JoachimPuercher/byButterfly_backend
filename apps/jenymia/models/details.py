"""Everything that belongs to one product and exists many times per product.

The foreign key sits here, not on Product; the related_name is how Product
reaches these rows. All of them carry sort_order, because the order on the
page is editorial, not alphabetical.
"""

from django.db import models

from apps.common.models import BaseModel

from .base import TranslationBase
from .choice_lists import (
    Availability,
    DataType,
    ProsConType,
    ServerRegion,
    SourceType,
)
from .product import Product


class ProductImage(BaseModel):
    class Source(models.TextChoices):
        MANUFACTURER = "manufacturer", "manufacturer"
        AMAZON_PAAPI = "amazon_paapi", "Amazon PA-API"
        AWIN = "awin", "Awin"
        OWN_PHOTO = "own_photo", "own photo"
        STOCK_LICENSED = "stock_licensed", "licensed stock"

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="images"
    )
    # Relative key inside the media bucket ("products/lego-duplo/hero.webp").
    # The serializer prefixes JENYMIA_MEDIA_BASE_URL, so moving the files to
    # another domain is one environment variable, not a data migration.
    key = models.CharField(max_length=300)
    source = models.CharField(max_length=20, choices=Source.choices)
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)
        constraints = [
            # At most one primary image per product, enforced by the database.
            models.UniqueConstraint(
                fields=["product"],
                condition=models.Q(is_primary=True),
                name="product_one_primary_image",
            )
        ]


class ProductImageTranslation(TranslationBase):
    image = models.ForeignKey(
        ProductImage, on_delete=models.CASCADE, related_name="translations"
    )
    alt_text = models.CharField(max_length=200)
    caption = models.CharField(max_length=300, blank=True)
    license_note = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["image", "locale"], name="image_one_translation_per_locale"
            )
        ]


class ProductSource(BaseModel):
    """The public source list under a published product: label, link, date.

    Built from the order's YoutubeUrl / WebUrl rows, never from the analysis
    output - a citation the model made up would be worse than none. The raw
    material itself stays internal; this is what readers and AI crawlers
    get to see, the strongest E-E-A-T signal on the page."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="sources"
    )
    # The source's own title, in the language it was published in - a
    # citation is not translated.
    label = models.CharField(max_length=300, blank=True)
    url = models.URLField(max_length=1000)
    source_type = models.ForeignKey(
        SourceType, on_delete=models.PROTECT, related_name="product_sources"
    )
    # When the source itself was published - an age signal for the citation.
    published_at = models.DateField(null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)


class ProductSpec(BaseModel):
    """Machine readable key/value facts. The key is stable, English and
    untranslated ("weight") - it is what compares products with each other.
    What a visitor reads, the label ("Gewicht") and the value, is
    translated."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="specs")
    key = models.CharField(max_length=60)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)
        constraints = [
            models.UniqueConstraint(
                fields=["product", "key"], name="product_spec_key_unique"
            )
        ]


class ProductSpecTranslation(TranslationBase):
    spec = models.ForeignKey(
        ProductSpec, on_delete=models.CASCADE, related_name="translations"
    )
    label = models.CharField(max_length=100)
    value = models.CharField(max_length=300)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["spec", "locale"], name="spec_one_translation_per_locale"
            )
        ]


class ProductFaq(BaseModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="faqs")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)


class ProductFaqTranslation(TranslationBase):
    faq = models.ForeignKey(
        ProductFaq, on_delete=models.CASCADE, related_name="translations"
    )
    question = models.CharField(max_length=300)
    answer = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["faq", "locale"], name="faq_one_translation_per_locale"
            )
        ]


class ProductProsCon(BaseModel):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="pros_cons"
    )
    type = models.ForeignKey(
        ProsConType, on_delete=models.PROTECT, related_name="pros_cons"
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        # By type follows ProsConType's own order: pros first, then cons.
        ordering = ("type", "sort_order")


class ProductProsConTranslation(TranslationBase):
    pros_con = models.ForeignKey(
        ProductProsCon, on_delete=models.CASCADE, related_name="translations"
    )
    text = models.CharField(max_length=300)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["pros_con", "locale"],
                name="pros_con_one_translation_per_locale",
            )
        ]


class AffiliateLink(BaseModel):
    """One offer per shop. Availability belongs here and not on Product: a
    product has no stock, a shop has."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="affiliate_links"
    )
    # The shop's name, the same in every language.
    provider = models.CharField(max_length=60)
    url = models.URLField(max_length=1000)
    availability = models.ForeignKey(
        Availability,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="affiliate_links",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)


class DataCategory(BaseModel):
    """Tech products only: which data a device or app collects, and where it
    ends up. Points at TechProduct, so no other group can carry one."""

    product = models.ForeignKey(
        "jenymia.TechProduct", on_delete=models.CASCADE, related_name="data_categories"
    )
    data_type = models.ForeignKey(
        DataType, on_delete=models.PROTECT, related_name="data_categories"
    )
    server_region = models.ForeignKey(
        ServerRegion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="data_categories",
    )
    is_optional = models.BooleanField(default=False)
    # Null means: not stated by the manufacturer, which differs from "no".
    third_party_sharing = models.BooleanField(null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)
        verbose_name_plural = "data categories"


class DataCategoryTranslation(TranslationBase):
    data_category = models.ForeignKey(
        DataCategory, on_delete=models.CASCADE, related_name="translations"
    )
    notes = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["data_category", "locale"],
                name="data_category_one_translation_per_locale",
            )
        ]
