from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone

from . import services
from .models import (
    AffiliateLink,
    Author,
    Badge,
    BadgeTranslation,
    Brand,
    DataCategory,
    DataCategoryTranslation,
    LearningBadge,
    LearningBadgeTranslation,
    Product,
    ProductFaq,
    ProductFaqTranslation,
    ProductImage,
    ProductImageTranslation,
    ProductProsCon,
    ProductProsConTranslation,
    ProductSource,
    ProductSpec,
    ProductSpecTranslation,
    ProductToAnalyse,
    ProductTranslation,
    SubCategory,
    SubCategoryTranslation,
    WebUrl,
    YoutubeUrl,
)


class SourceUrlInline(admin.TabularInline):
    """Shared layout for the URL inlines: the admin enters url and date,
    the pipeline fills raw_text and the status, so those stay read-only.
    A hand-entered source_date wins over what the pipeline finds."""

    extra = 1
    fields = ("url", "source_date", "extract_status", "raw_text", "error")
    readonly_fields = ("extract_status", "raw_text", "error")


class YoutubeUrlInline(SourceUrlInline):
    model = YoutubeUrl
    verbose_name = "YouTube URL"
    verbose_name_plural = "YouTube URLs"


class WebUrlInline(SourceUrlInline):
    model = WebUrl
    verbose_name = "Web URL"
    verbose_name_plural = "Web URLs"
    # The operator says what kind of page it is; the fetcher cannot know.
    fields = (
        "url",
        "source_type",
        "source_date",
        "extract_status",
        "raw_text",
        "error",
    )


@admin.register(ProductToAnalyse)
class ProductToAnalyseAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "brand",
        "primary_category",
        "status",
        "analysed_by",
        "creator",
        "last_analysed_at",
    )
    list_filter = ("status", "analysed_by", "primary_category")
    search_fields = ("title", "brand")
    ordering = ("-created_at",)
    readonly_fields = (
        "status",
        "analysed_by",
        "error",
        "attempts",
        "prompt_version",
        "llm_provider",
        "llm_model",
        "creator",
        "last_analysed_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        # The main category decides which prompt runs and becomes the
        # product's home, so it is chosen here by hand and never guessed by
        # the analysis. The sub-category is optional and only needed when a
        # group of products inside it has its own prompt.
        (None, {"fields": ("title", "brand", "primary_category", "sub_category")}),
        (
            "Analysis",
            {
                "fields": (
                    "status",
                    "error",
                    "attempts",
                    "prompt_version",
                    "llm_provider",
                    "llm_model",
                    "analysed_by",
                    "last_analysed_at",
                )
            },
        ),
        ("Meta", {"fields": ("creator", "created_at", "updated_at")}),
    )
    inlines = (YoutubeUrlInline, WebUrlInline)
    actions = ("re_analyse", "re_extract")

    @admin.action(description="Analyse again (downloads and transcribes again)")
    def re_analyse(self, request, queryset):
        for order in queryset:
            services.request_analysis(order)
        self.message_user(request, f"{queryset.count()} order(s) queued.")

    @admin.action(description="Evaluate again (reuses the stored texts)")
    def re_extract(self, request, queryset):
        # Skips download and transcription: the raw text is already in the
        # database, only the prompt runs again. This is the cheap way to try
        # a corrected prompt.
        started = 0
        for order in queryset:
            if not order.youtube_urls.exists() and not order.web_urls.exists():
                self.message_user(
                    request,
                    f"{order} has no sources; run 'Analyse again' first.",
                    level=messages.WARNING,
                )
                continue
            services.request_extract(order)
            started += 1
        self.message_user(request, f"{started} order(s) sent to the analysis.")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("creator", "sub_category")

    def save_model(self, request, obj, form, change):
        # Entries made here are always admin-created; the creator is the
        # logged-in user, never editable by hand.
        if not change:
            obj.creator = request.user
            obj.analysed_by = ProductToAnalyse.AnalysedBy.ADMIN
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        # Only after the inlines are saved does the order have its URLs -
        # queueing before that would start a run without sources.
        super().save_related(request, form, formsets, change)
        if not change:
            services.request_analysis(form.instance)


class SubCategoryTranslationInline(admin.TabularInline):
    model = SubCategoryTranslation
    extra = 2
    fields = ("locale", "slug", "name")


@admin.register(SubCategory)
class SubCategoryAdmin(admin.ModelAdmin):
    """The analysis proposes these; they are corrected here before a product
    goes public. Flat, so one sub-category can sit on products of different
    main categories - that is what makes a product visible in more than one
    group.

    prompt_name stays empty unless this group needs its own prompt file."""

    list_display = ("__str__", "prompt_name", "sort_order")
    ordering = ("sort_order",)
    fields = ("prompt_name", "sort_order")
    inlines = (SubCategoryTranslationInline,)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")


# --- product ---------------------------------------------------------------
#
# The pipeline writes a product unpublished; this is where it is read,
# corrected and released. Django has no nested inlines, so the rows that carry
# their own translations (specs, FAQs, pros/cons, data categories, images) are
# listed on the product and edited on their own page, where their translation
# inline sits.


class ProductTranslationInline(admin.StackedInline):
    model = ProductTranslation
    extra = 0
    fields = (
        "locale",
        "slug",
        "title",
        "hook",
        "summary",
        "verdict",
        "question_headline",
        "description_short",
        "description_detail",
        "meta_title",
        "meta_description",
        "safety_short",
        "safety_long",
        "growth_info",
        "privacy_short",
        "privacy_long",
    )


class ProductSourceInline(admin.TabularInline):
    """Built from the order's own URLs, never from the analysis text. Editable
    because a label may need correcting, not because rows should be added."""

    model = ProductSource
    extra = 0
    fields = ("sort_order", "label", "url", "source_type", "published_at")


class ProductImageInline(admin.TabularInline):
    """key is the relative path inside the media bucket ("products/x/hero.webp").
    The file itself lives on R2; the database never holds an absolute URL."""

    model = ProductImage
    extra = 0
    fields = ("sort_order", "key", "source", "license_note", "is_primary")
    show_change_link = True


class AffiliateLinkInline(admin.TabularInline):
    model = AffiliateLink
    extra = 0
    fields = ("sort_order", "provider", "url", "availability")


class ProductSpecInline(admin.TabularInline):
    model = ProductSpec
    extra = 0
    fields = ("sort_order", "key")
    show_change_link = True


class ProductFaqInline(admin.TabularInline):
    model = ProductFaq
    extra = 0
    fields = ("sort_order",)
    show_change_link = True


class ProductProsConInline(admin.TabularInline):
    model = ProductProsCon
    extra = 0
    fields = ("sort_order", "type")
    show_change_link = True


class DataCategoryInline(admin.TabularInline):
    model = DataCategory
    extra = 0
    fields = (
        "sort_order",
        "data_type",
        "server_region",
        "is_optional",
        "third_party_sharing",
    )
    show_change_link = True


class ProductAdminForm(forms.ModelForm):
    """Lets the publication fields be edited by hand without losing the
    checks.

    Ticking is_published here is the same act as running the publish action,
    so it goes through the same rules (services.check_publishable). The two
    dates are filled in when they are empty and left alone when they carry a
    value, so a publication date can be corrected afterwards.
    """

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("is_published"):
            return cleaned

        try:
            services.check_publishable(self.instance, cleaned.get("ampel_score"))
        except ValueError as error:
            raise ValidationError({"is_published": str(error)}) from None

        now = timezone.now()
        cleaned["published_at"] = cleaned.get("published_at") or now
        cleaned["last_verified_at"] = cleaned.get("last_verified_at") or now
        return cleaned


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
    list_display = (
        "__str__",
        "brand",
        "primary_category",
        "ampel_score",
        "is_published",
        "published_at",
    )
    list_filter = (
        "is_published",
        "ampel_score",
        "primary_category",
        "sub_categories",
    )
    search_fields = ("translations__title", "brand__name", "model_name", "gtin")
    # Only the three the database fills itself; everything else on the model
    # is editable here.
    readonly_fields = ("id", "created_at", "updated_at")
    filter_horizontal = ("sub_categories", "badges", "learning_badges")
    fieldsets = (
        (None, {"fields": ("brand", "model_name", "gtin", "author", "ampel_score")}),
        (
            "Price",
            {
                "fields": ("price_official", "price_checked_at"),
                "description": (
                    "The manufacturer's list price, shown on the page as a "
                    "reference next to the shop buttons. Shop prices are not "
                    "maintained here."
                ),
            },
        ),
        (
            "Suitability",
            {
                "fields": (
                    "age_min_months",
                    "age_max_months",
                    "usage_lifespan_months",
                    "manufactured_in_country",
                    "is_offline_capable",
                    "requires_account",
                    "is_child_certified",
                )
            },
        ),
        (
            "Classification",
            {
                "fields": (
                    "primary_category",
                    "sub_categories",
                    "badges",
                    "learning_badges",
                ),
                "description": (
                    "The main category is the breadcrumb and the canonical "
                    "home, one of three. Sub-categories are flat and are what "
                    "make the product show up in another group as well."
                ),
            },
        ),
        (
            "Publication",
            {
                "fields": ("is_published", "published_at", "last_verified_at"),
                "description": (
                    "Publishing needs a traffic light score and both "
                    "translations. Empty dates are filled on publication."
                ),
            },
        ),
        ("Meta", {"fields": ("analysis", "id", "created_at", "updated_at")}),
    )
    inlines = (
        ProductTranslationInline,
        ProductSpecInline,
        ProductFaqInline,
        ProductProsConInline,
        DataCategoryInline,
        ProductSourceInline,
        ProductImageInline,
        AffiliateLinkInline,
    )
    actions = ("publish", "unpublish")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("brand", "author")
            .prefetch_related("translations")
        )

    @admin.action(description="Publish")
    def publish(self, request, queryset):
        published = 0
        for product in queryset:
            try:
                services.publish_product(product)
            except ValueError as error:
                self.message_user(request, f"{product}: {error}", level=messages.ERROR)
                continue
            published += 1
        if published:
            self.message_user(request, f"{published} product(s) published.")

    @admin.action(description="Withdraw")
    def unpublish(self, request, queryset):
        for product in queryset:
            services.unpublish_product(product)
        self.message_user(request, f"{queryset.count()} product(s) withdrawn.")


# --- detail rows with their own translations -------------------------------


class SpecTranslationInline(admin.TabularInline):
    model = ProductSpecTranslation
    extra = 0
    fields = ("locale", "value")


@admin.register(ProductSpec)
class ProductSpecAdmin(admin.ModelAdmin):
    list_display = ("key", "product", "sort_order")
    inlines = (SpecTranslationInline,)


class FaqTranslationInline(admin.TabularInline):
    model = ProductFaqTranslation
    extra = 0
    fields = ("locale", "question", "answer")


@admin.register(ProductFaq)
class ProductFaqAdmin(admin.ModelAdmin):
    list_display = ("__str__", "product", "sort_order")
    inlines = (FaqTranslationInline,)


class ProsConTranslationInline(admin.TabularInline):
    model = ProductProsConTranslation
    extra = 0
    fields = ("locale", "text")


@admin.register(ProductProsCon)
class ProductProsConAdmin(admin.ModelAdmin):
    list_display = ("__str__", "product", "type", "sort_order")
    inlines = (ProsConTranslationInline,)


class DataCategoryTranslationInline(admin.TabularInline):
    model = DataCategoryTranslation
    extra = 0
    fields = ("locale", "notes")


@admin.register(DataCategory)
class DataCategoryAdmin(admin.ModelAdmin):
    list_display = ("data_type", "product", "server_region", "sort_order")
    inlines = (DataCategoryTranslationInline,)


class ImageTranslationInline(admin.TabularInline):
    model = ProductImageTranslation
    extra = 0
    fields = ("locale", "alt_text", "caption")


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ("key", "product", "source", "is_primary", "sort_order")
    inlines = (ImageTranslationInline,)


# --- master data -----------------------------------------------------------
#
# Registered so the product form can reach them: Django only offers the "add"
# and "edit" buttons next to a relation when the related model has an admin.
# The pipeline creates brands, badges and learning badges on its own, so
# without these pages there would be rows nobody can correct.


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "logo_key")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    fields = ("name", "slug", "logo_key")


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    """The person who signs a published analysis - schema.org/Person, the
    E-E-A-T signal. same_as holds profile URLs as a JSON list."""

    list_display = ("name", "role", "slug")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    fields = ("name", "slug", "role", "bio", "credentials", "photo_key", "same_as")


class BadgeTranslationInline(admin.TabularInline):
    model = BadgeTranslation
    extra = 2
    fields = ("locale", "name", "description")


@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    """Test marks and properties (CE, GS, FSC, ...)."""

    list_display = ("slug", "sort_order")
    ordering = ("sort_order", "slug")
    fields = ("slug", "sort_order")
    inlines = (BadgeTranslationInline,)


class LearningBadgeTranslationInline(admin.TabularInline):
    model = LearningBadgeTranslation
    extra = 2
    fields = ("locale", "name")


@admin.register(LearningBadge)
class LearningBadgeAdmin(admin.ModelAdmin):
    """Areas of development: coordination, logic, creativity, language,
    fine-motor, social-emotional, concentration."""

    list_display = ("slug", "sort_order")
    ordering = ("sort_order", "slug")
    fields = ("slug", "sort_order")
    inlines = (LearningBadgeTranslationInline,)
