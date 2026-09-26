"""Read serializers for the public product detail endpoint.

One serializer per use case. Nothing here is writable, and nothing here
exposes raw source text: transcripts and scraped page text stay in the
backend, only our own wording and the link to the source go out.

Every translated table is prefetched by the selector, so picking the right
language happens in Python and costs no extra query.
"""

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist
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


def translated_value(
    row, field: str, locale: str, relation: str = "translations"
) -> str | None:
    """One field of the row's translation in the given language, read
    through `relation` (a product's group texts are "group_translations").

    A missing translation yields null rather than an empty string: the
    frontend should be able to tell "not translated yet" from "empty".
    Reads from the prefetched translations, so it costs no query.
    """
    if row is None:
        return None
    translation = next(
        (t for t in getattr(row, relation).all() if t.locale == locale), None
    )
    return getattr(translation, field) if translation else None


class TranslatedMixin:
    """Merges the fields of the matching <Model>Translation row into the
    output. `translated_fields` lists which ones.

    `choice_labels` names foreign keys to choice lists (the field keeps its
    machine value, the slug or code); each gets a `<field>_label` with the
    list's label in the requested language. Every word a visitor reads comes
    from the database this way - nothing is left for the frontend to
    translate.
    """

    translated_fields: tuple[str, ...] = ()
    choice_labels: tuple[str, ...] = ()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        locale = self.context["locale"]
        for field in self.translated_fields:
            data[field] = translated_value(instance, field, locale)
        for field in self.choice_labels:
            data[f"{field}_label"] = translated_value(
                getattr(instance, field), "label", locale
            )
        return data


class BrandSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = Brand
        fields = ("slug", "name", "logo_url")

    def get_logo_url(self, brand: Brand) -> str:
        return media_url(brand.logo_key)


class AuthorSerializer(TranslatedMixin, serializers.ModelSerializer):
    """The person who signs the analysis - schema.org/Person for E-E-A-T."""

    translated_fields = ("role", "bio", "credentials")
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Author
        fields = ("slug", "name", "photo_url", "same_as")

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
    translated_fields = ("alt_text", "caption", "license_note")
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ("url", "source", "is_primary", "sort_order")

    def get_url(self, image: ProductImage) -> str:
        return media_url(image.key)


class SourceSerializer(TranslatedMixin, serializers.ModelSerializer):
    """The citation, never the material. raw_text is deliberately absent.
    The label is the source's own title and stays in its language."""

    choice_labels = ("source_type",)
    source_type = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = ProductSource
        fields = ("label", "url", "source_type", "published_at")


class SpecSerializer(TranslatedMixin, serializers.ModelSerializer):
    """key compares products and is not shown; label and value are."""

    translated_fields = ("label", "value")

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
    choice_labels = ("type",)
    type = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = ProductProsCon
        fields = ("type",)


class AffiliateLinkSerializer(TranslatedMixin, serializers.ModelSerializer):
    choice_labels = ("availability",)
    availability = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = AffiliateLink
        fields = ("provider", "url", "availability")


class DataCategorySerializer(TranslatedMixin, serializers.ModelSerializer):
    translated_fields = ("notes",)
    choice_labels = ("data_type", "server_region")
    data_type = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    server_region = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = DataCategory
        fields = ("data_type", "server_region", "is_optional", "third_party_sharing")


def main_category_fields(product: Product, locale: str) -> dict[str, str | None]:
    """Name and hub URL slug of the product's main category in one
    language, next to its key."""
    return {
        "primary_category_name": translated_value(
            product.primary_category, "name", locale
        ),
        "primary_category_slug": translated_value(
            product.primary_category, "slug", locale
        ),
    }


class ProductListSerializer(TranslatedMixin, serializers.ModelSerializer):
    """A product card in a list: headline, hook, category and badges."""

    translated_fields = ("title", "hook")

    primary_category = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    badges = BadgeSerializer(many=True)

    class Meta:
        model = Product
        fields = ("id", "primary_category", "badges")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.update(main_category_fields(instance, self.context["locale"]))
        return data


def group_column(product: Product, name: str) -> bool:
    """Whether the product's group has this column. Asked of the model, not
    answered with a getattr default, so a misspelt name raises."""
    try:
        type(product)._meta.get_field(name)
    except FieldDoesNotExist:
        return False
    return True


class ProductDetailSerializer(TranslatedMixin, serializers.ModelSerializer):
    """The full product detail page in one response, for a product of any
    group (ToysProduct, SchoolProduct, TechProduct - see
    selectors.published_product).

    Every key is present for every product, as the v1 contract demands.
    What the product's group does not have is null - a flag or a text of
    another group - or an empty list.
    """

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
    )
    # The texts of one group only, read from its group_translations.
    group_texts = (
        "safety_short",
        "safety_long",
        "growth_info",
        "privacy_short",
        "privacy_long",
    )
    choice_labels = ("ampel_score", "manufactured_in_country")

    # Each keeps its machine value (score, ISO code, key); the label comes
    # alongside as <field>_label, the main category's as name and slug.
    ampel_score = serializers.SlugRelatedField(slug_field="value", read_only=True)
    manufactured_in_country = serializers.SlugRelatedField(
        slug_field="code", read_only=True
    )
    primary_category = serializers.SlugRelatedField(slug_field="slug", read_only=True)

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
    # Columns and lists of one group; see the getters below.
    is_offline_capable = serializers.SerializerMethodField()
    requires_account = serializers.SerializerMethodField()
    is_child_certified = serializers.SerializerMethodField()
    learning_badges = serializers.SerializerMethodField()
    data_categories = serializers.SerializerMethodField()
    images = ImageSerializer(many=True)
    primary_image = serializers.SerializerMethodField()
    sources = SourceSerializer(many=True)
    sources_count = serializers.SerializerMethodField()
    specs = SpecSerializer(many=True)
    faqs = FaqSerializer(many=True)
    pros_cons = ProsConSerializer(many=True)
    affiliate_links = AffiliateLinkSerializer(many=True)

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

    def to_representation(self, instance):
        if type(instance) is Product:
            raise TypeError(
                "The detail serializer needs the product's group; load it "
                "through selectors.published_product."
            )
        data = super().to_representation(instance)
        locale = self.context["locale"]
        group_columns = {
            f.name for f in instance.group_translations.model._meta.concrete_fields
        }
        for name in self.group_texts:
            data[name] = (
                translated_value(instance, name, locale, "group_translations")
                if name in group_columns
                else None
            )
        data.update(main_category_fields(instance, locale))
        return data

    def get_is_offline_capable(self, product: Product) -> bool | None:
        return (
            product.is_offline_capable
            if group_column(product, "is_offline_capable")
            else None
        )

    def get_requires_account(self, product: Product) -> bool | None:
        return (
            product.requires_account
            if group_column(product, "requires_account")
            else None
        )

    def get_is_child_certified(self, product: Product) -> bool | None:
        return (
            product.is_child_certified
            if group_column(product, "is_child_certified")
            else None
        )

    def get_learning_badges(self, product: Product) -> list[dict]:
        if not group_column(product, "learning_badges"):
            return []
        return LearningBadgeSerializer(
            product.learning_badges.all(), many=True, context=self.context
        ).data

    def get_data_categories(self, product: Product) -> list[dict]:
        if not group_column(product, "data_categories"):
            return []
        return DataCategorySerializer(
            product.data_categories.all(), many=True, context=self.context
        ).data

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
