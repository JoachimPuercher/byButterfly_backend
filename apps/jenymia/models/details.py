"""Everything that belongs to one product and exists many times per product.

The foreign key sits here, not on Product; the related_name is how Product
reaches these rows. All of them carry sort_order, because the order on the
page is editorial, not alphabetical.
"""

from django.db import models

from apps.common.models import BaseModel

from .base import SourceType, TranslationBase
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
    license_note = models.TextField(blank=True)
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
    label = models.CharField(max_length=300, blank=True)
    url = models.URLField(max_length=1000)
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    # When the source itself was published - an age signal for the citation.
    published_at = models.DateField(null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)


class ProductSpec(BaseModel):
    """Machine readable key/value facts. The key is stable and untranslated
    ("material"), the value is translated."""

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
    class Type(models.TextChoices):
        PRO = "pro", "pro"
        CON = "con", "con"

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="pros_cons"
    )
    type = models.CharField(max_length=3, choices=Type.choices)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
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

    class Availability(models.TextChoices):
        IN_STOCK = "in_stock", "in stock"
        OUT_OF_STOCK = "out_of_stock", "out of stock"
        PREORDER = "preorder", "preorder"
        DISCONTINUED = "discontinued", "discontinued"

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="affiliate_links"
    )
    provider = models.CharField(max_length=60)
    url = models.URLField(max_length=1000)
    availability = models.CharField(
        max_length=15, choices=Availability.choices, blank=True
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("sort_order",)


class DataCategory(BaseModel):
    """Tech pipeline only: which data a device or app collects, and where it
    ends up."""

    class DataType(models.TextChoices):
        LOCATION = "location", "location"
        AUDIO = "audio", "audio"
        VIDEO = "video", "video"
        CONTACTS = "contacts", "contacts"
        USAGE_STATS = "usage_stats", "usage statistics"
        BIOMETRICS = "biometrics", "biometrics"
        MESSAGES = "messages", "messages"
        PHOTOS = "photos", "photos"

    class ServerRegion(models.TextChoices):
        EU = "EU", "EU"
        US = "US", "US"
        CN = "CN", "CN"
        THIRD_COUNTRY = "third_country", "third country"
        UNKNOWN = "unknown", "unknown"
        ON_DEVICE_ONLY = "on_device_only", "on device only"

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="data_categories"
    )
    data_type = models.CharField(max_length=15, choices=DataType.choices)
    server_region = models.CharField(
        max_length=15, choices=ServerRegion.choices, blank=True
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
