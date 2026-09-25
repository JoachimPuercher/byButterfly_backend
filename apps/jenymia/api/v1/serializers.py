"""Read serializers for the public product detail endpoint.

One serializer per use case. Nothing here is writable, and nothing here
exposes raw source text: transcripts and scraped page text stay in the
backend, only our own wording and the link to the source go out.

Every translated table is prefetched by the selector, so picking the right
language happens in Python and costs no extra query.
"""

from django.conf import settings
from rest_framework import serializers

from apps.jenymia.models import (
    PRICE_CURRENCY,
    AffiliateLink,
    Author,
    Badge,
    Brand,
    DataCategory,
    LearningBadge,
    Product,
    ProductFaq,
    ProductImage,
    ProductProsCon,
    ProductSource,
    ProductSpec,
    SubCategory,
)


def media_url(key: str) -> str:
    """The database holds relative keys only; the domain comes from the
    environment, so moving the bucket is a variable, not a migration."""
    if not key:
        return ""
    return f"{settings.JENYMIA_MEDIA_BASE_URL.rstrip('/')}/{key.lstrip('/')}"


class TranslatedMixin:
    """Merges the fields of the matching <Model>Translation row into the
    output. `translated_fields` lists which ones.

    A missing translation yields null rather than an empty string: the
    frontend should be able to tell "not translated yet" from "empty".
    """

    translated_fields: tuple[str, ...] = ()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        locale = self.context["locale"]
        translation = next(
            (t for t in instance.translations.all() if t.locale == locale), None
        )
        for field in self.translated_fields:
            data[field] = getattr(translation, field) if translation else None
        return data


class BrandSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = Brand
        fields = ("slug", "name", "logo_url")

    def get_logo_url(self, brand: Brand) -> str:
        return media_url(brand.logo_key)


class AuthorSerializer(serializers.ModelSerializer):
    """The person who signs the analysis - schema.org/Person for E-E-A-T."""

    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Author
        fields = ("slug", "name", "role", "bio", "credentials", "photo_url", "same_as")

    def get_photo_url(self, author: Author) -> str:
        return media_url(author.photo_key)


class SubCategorySerializer(TranslatedMixin, serializers.ModelSerializer):
    """Flat, so there is no chain to follow: slug and name in the requested
    language are everything a filter chip or a hub link needs."""

    translated_fields = ("slug", "name")

    class Meta:
        model = SubCategory
        fields = ("id",)


class BadgeSerializer(TranslatedMixin, serializers.ModelSerializer):
    translated_fields = ("name", "description")

    class Meta:
        model = Badge
        fields = ("slug",)


class LearningBadgeSerializer(TranslatedMixin, serializers.ModelSerializer):
    translated_fields = ("name",)

    class Meta:
        model = LearningBadge
        fields = ("slug",)


class ImageSerializer(TranslatedMixin, serializers.ModelSerializer):
    translated_fields = ("alt_text", "caption")
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ("url", "source", "license_note", "is_primary", "sort_order")

    def get_url(self, image: ProductImage) -> str:
        return media_url(image.key)


class SourceSerializer(serializers.ModelSerializer):
    """The citation, never the material. raw_text is deliberately absent."""

    class Meta:
        model = ProductSource
        fields = ("label", "url", "source_type", "published_at")


class SpecSerializer(TranslatedMixin, serializers.ModelSerializer):
    translated_fields = ("value",)

    class Meta:
        model = ProductSpec
        fields = ("key",)


class FaqSerializer(TranslatedMixin, serializers.ModelSerializer):
    translated_fields = ("question", "answer")

    class Meta:
        model = ProductFaq
        fields = ()


class ProsConSerializer(TranslatedMixin, serializers.ModelSerializer):
    translated_fields = ("text",)

    class Meta:
        model = ProductProsCon
        fields = ("type",)


class AffiliateLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = AffiliateLink
        fields = ("provider", "url", "availability")


class DataCategorySerializer(TranslatedMixin, serializers.ModelSerializer):
    translated_fields = ("notes",)

    class Meta:
        model = DataCategory
        fields = ("data_type", "server_region", "is_optional", "third_party_sharing")


class ProductDetailSerializer(TranslatedMixin, serializers.ModelSerializer):
    """The full product detail page in one response."""

    translated_fields = (
        "slug",
        "title",
        "hook",
        "description_short",
        "description_detail",
        "meta_title",
        "meta_description",
        "summary",
        "verdict",
        "question_headline",
        "safety_short",
        "safety_long",
        "growth_info",
        "privacy_short",
        "privacy_long",
    )

    locale = serializers.SerializerMethodField()
    # Slug of the same product in the other languages - the frontend needs
    # them for hreflang, otherwise the language versions compete with each
    # other in the search index.
    alternates = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()

    brand = BrandSerializer()
    author = AuthorSerializer()
    sub_categories = SubCategorySerializer(many=True)
    badges = BadgeSerializer(many=True)
    learning_badges = LearningBadgeSerializer(many=True)
    images = ImageSerializer(many=True)
    primary_image = serializers.SerializerMethodField()
    sources = SourceSerializer(many=True)
    sources_count = serializers.SerializerMethodField()
    specs = SpecSerializer(many=True)
    faqs = FaqSerializer(many=True)
    pros_cons = ProsConSerializer(many=True)
    affiliate_links = AffiliateLinkSerializer(many=True)
    data_categories = DataCategorySerializer(many=True)

    class Meta:
        model = Product
        fields = (
            "id",
            "locale",
            "alternates",
            "ampel_score",
            "price",
            "gtin",
            "model_name",
            "published_at",
            "updated_at",
            "last_verified_at",
            "age_min_months",
            "age_max_months",
            "usage_lifespan_months",
            "manufactured_in_country",
            "is_offline_capable",
            "requires_account",
            "is_child_certified",
            "brand",
            "author",
            # The value, not an object: three fixed groups whose display name
            # the frontend translates.
            "primary_category",
            "sub_categories",
            "badges",
            "learning_badges",
            "images",
            "primary_image",
            "sources",
            "sources_count",
            "specs",
            "faqs",
            "pros_cons",
            "affiliate_links",
            "data_categories",
        )

    def get_locale(self, product: Product) -> str:
        return self.context["locale"]

    def get_alternates(self, product: Product) -> dict[str, str]:
        return {
            t.locale: t.slug
            for t in product.translations.all()
            if t.locale != self.context["locale"]
        }

    def get_price(self, product: Product) -> dict:
        """The manufacturer's list price and when it was checked.

        Shown on the page next to the shop buttons as a reference. It must
        NOT end up in the JSON-LD as offers.price: a price in the search
        result answers the question before the visitor sees the cheaper shop
        offers on the page. Once affiliate prices are fetched automatically,
        an AggregateOffer with lowPrice takes its place.
        """
        return {
            # A string, not a number: DRF's JSON encoder turns a Decimal into
            # a float, and 29.99 has no exact binary representation.
            "official": (
                str(product.price_official)
                if product.price_official is not None
                else None
            ),
            "currency": PRICE_CURRENCY,
            "checked_at": product.price_checked_at,
        }

    def get_primary_image(self, product: Product) -> dict | None:
        image = next((i for i in product.images.all() if i.is_primary), None)
        if image is None:
            return None
        return ImageSerializer(image, context=self.context).data

    def get_sources_count(self, product: Product) -> int:
        return len(product.sources.all())
