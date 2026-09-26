from typing import Any

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Model
from django.shortcuts import redirect
from django.utils.html import format_html, format_html_join

from . import selectors, services
from .models import (
    YOUTUBE_SOURCE_TYPE,
    AffiliateLink,
    AmpelScore,
    AmpelScoreTranslation,
    Author,
    AuthorTranslation,
    Availability,
    AvailabilityTranslation,
    Badge,
    BadgeTranslation,
    Brand,
    Country,
    CountryTranslation,
    DataCategory,
    DataCategoryTranslation,
    DataType,
    DataTypeTranslation,
    LearningBadge,
    LearningBadgeTranslation,
    Locale,
    MainCategory,
    MainCategoryTranslation,
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
    ProsConType,
    ProsConTypeTranslation,
    SchoolProduct,
    SchoolProductTranslation,
    ServerRegion,
    ServerRegionTranslation,
    SourceType,
    SourceTypeTranslation,
    SubCategory,
    SubCategoryTranslation,
    TechProduct,
    TechProductTranslation,
    ToysProduct,
    ToysProductTranslation,
    WebUrl,
    YoutubeUrl,
)
from .pipeline_shared.analysis import schema

# --- translations in every language ------------------------------------------
#
# Every word the site shows comes from the database in both languages. So
# every form that edits translations requires them all: a row per language,
# and a text given in one language given in the other. Publishing checks the
# same rules once more (services.publishing_problems).

# SlugField and the text areas of TextField columns are CharFields too.
TEXT_FIELD_TYPES = (forms.CharField,)


def one_sided_texts(values: dict[str, dict[str, str]]) -> list[tuple[str, str]]:
    """(field, locale) of every text that is filled in one language and
    empty in another. `values` maps locale -> field -> text."""
    fields = {name for texts in values.values() for name in texts}
    gaps = []
    for name in sorted(fields):
        texts = {locale: (values[locale].get(name) or "").strip() for locale in values}
        if any(texts.values()):
            gaps += [(name, locale) for locale, text in texts.items() if not text]
    return gaps


class BothLocalesFormSet(forms.BaseInlineFormSet):
    """Translation rows, one per language: every language present, none
    twice, and no text in one language only. New rows come with the missing
    language already chosen."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        present = (
            set(self.get_queryset().values_list("locale", flat=True))
            if self.instance.pk
            else set()
        )
        self.initial_extra = [
            {"locale": locale} for locale in Locale.values if locale not in present
        ]

    def _construct_form(self, i: int, **kwargs: Any) -> forms.Form:
        form = super()._construct_form(i, **kwargs)
        if i >= self.initial_form_count():
            # A missing language is not an optional extra row: it is
            # validated and saved even with its texts left empty. Where a
            # column may be empty (a group's safety text) the row is simply
            # stored; where it may not, the column reports itself.
            form.has_changed = lambda: True
        return form

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        kept = [
            form
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        locales = [form.cleaned_data["locale"] for form in kept]
        missing = [locale for locale in Locale.values if locale not in locales]
        if missing:
            raise ValidationError(
                f"Enter the {' and '.join(missing)} translation as well - "
                "every text is shown in every language."
            )
        if len(locales) != len(set(locales)):
            raise ValidationError("Enter each language only once.")
        values = {
            form.cleaned_data["locale"]: {
                name: form.cleaned_data.get(name)
                for name, field in form.fields.items()
                if isinstance(field, TEXT_FIELD_TYPES) and name != "locale"
            }
            for form in kept
        }
        gaps = one_sided_texts(values)
        if gaps:
            raise ValidationError(
                [
                    f"{name} ({locale}) is empty, but filled in the other language."
                    for name, locale in gaps
                ]
            )


def problem_list(heading: str, problems: list[str]) -> str:
    """An admin message: a heading and one line per problem."""
    return format_html(
        "{}<ul>{}</ul>",
        heading,
        format_html_join("", "<li>{}</li>", ((problem,) for problem in problems)),
    )


class BothLocalesInline:
    """Mixin for an inline of translation rows (one row per language).
    `translations_name` is the related_name the rows hang from on the parent;
    a product's group texts hang from "group_translations"."""

    formset = BothLocalesFormSet
    max_num = len(Locale.values)
    translations_name = "translations"

    def get_extra(self, request, obj=None, **kwargs) -> int:
        present = (
            set(getattr(obj, self.translations_name).values_list("locale", flat=True))
            if obj
            else set()
        )
        return len([locale for locale in Locale.values if locale not in present])


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
        return (
            super()
            .get_queryset(request)
            .select_related("creator", "sub_category", "primary_category")
            .prefetch_related("primary_category__translations")
        )

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


class SubCategoryTranslationInline(BothLocalesInline, admin.TabularInline):
    model = SubCategoryTranslation
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
# corrected and released - all of it on the one page of the product's group
# (ToysProduct, SchoolProduct, TechProduct). Django has no nested inlines, so
# the rows that carry their own translations (specs, FAQs, pros/cons, data
# categories, images) use TranslatedRowInline: both languages sit as extra
# columns in the row itself.


class ProductTranslationInline(BothLocalesInline, admin.StackedInline):
    """The texts every product has. The slug is editable: it is built from
    the title once, and a title corrected later does not move it."""

    model = ProductTranslation
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
    )


class GroupTextInline(BothLocalesInline, admin.StackedInline):
    """The texts only this product group has, one row per language."""

    translations_name = "group_translations"


class ToysTextInline(GroupTextInline):
    model = ToysProductTranslation
    verbose_name_plural = "toys texts (safety, growth)"
    fields = ("locale", "safety_short", "safety_long", "growth_info")


class SchoolTextInline(GroupTextInline):
    model = SchoolProductTranslation
    verbose_name_plural = "school texts (safety)"
    fields = ("locale", "safety_short", "safety_long")


class TechTextInline(GroupTextInline):
    model = TechProductTranslation
    verbose_name_plural = "tech texts (privacy)"
    fields = ("locale", "privacy_short", "privacy_long")


class ProductSourceInline(admin.TabularInline):
    """Built from the order's own URLs, never from the analysis text. Editable
    because a label may need correcting, not because rows should be added."""

    model = ProductSource
    extra = 0
    fields = ("sort_order", "label", "url", "source_type", "published_at")


class AffiliateLinkInline(admin.TabularInline):
    model = AffiliateLink
    extra = 0
    fields = ("sort_order", "provider", "url", "availability")


class TranslatedRowForm(forms.ModelForm):
    """A detail row and its translations in one form.

    A FAQ is an inline of the product, but its question and answer live one
    level further down, in ProductFaqTranslation. This form carries those
    fields as <field>_<locale> next to the row's own columns, fills them from
    the stored translations and writes them back on save. The concrete form
    per model is built by translated_row_form().
    """

    translation_model: type[Model]
    translation_fk: str
    translated_fields: tuple[str, ...]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            for translation in self.instance.translations.all():
                for name in self.translated_fields:
                    self.initial.setdefault(
                        f"{name}_{translation.locale}", getattr(translation, name)
                    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        values = {
            locale: {
                name: cleaned.get(f"{name}_{locale}") for name in self.translated_fields
            }
            for locale in Locale.values
        }
        for name, locale in one_sided_texts(values):
            self.add_error(
                f"{name}_{locale}", "Filled in the other language - enter it here too."
            )
        return cleaned

    def save(self, commit: bool = True) -> Model:
        row = super().save(commit=commit)
        if commit:
            self._save_translations(row)
            return row
        # Without commit the row has no primary key yet, so the translations
        # follow once the caller saves it and calls save_m2m().
        save_m2m = self.save_m2m

        def save_m2m_and_translations() -> None:
            save_m2m()
            self._save_translations(row)

        self.save_m2m = save_m2m_and_translations
        return row

    def _save_translations(self, row: Model) -> None:
        services.save_row_translations(
            row,
            self.translation_model,
            self.translation_fk,
            {
                locale: {
                    name: self.cleaned_data[f"{name}_{locale}"]
                    for name in self.translated_fields
                }
                for locale in Locale.values
            },
        )


def translated_row_form(
    translation_model: type[Model],
    translation_fk: str,
    translated_fields: tuple[str, ...],
) -> type[TranslatedRowForm]:
    """A TranslatedRowForm with one form field per translated column and
    locale, each derived from the model field - length limit, required and
    widget included."""
    attrs: dict[str, Any] = {
        "translation_model": translation_model,
        "translation_fk": translation_fk,
        "translated_fields": translated_fields,
    }
    for name in translated_fields:
        model_field = translation_model._meta.get_field(name)
        for locale in Locale.values:
            form_field = model_field.formfield()
            if isinstance(form_field.widget, forms.Textarea):
                form_field.widget.attrs.update(rows=3, cols=40)
            form_field.label = f"{model_field.verbose_name} ({locale})"
            attrs[f"{name}_{locale}"] = form_field
    return type(f"{translation_model.__name__}RowForm", (TranslatedRowForm,), attrs)


class TranslatedRowInline(admin.TabularInline):
    """A detail row edited right on the product page, both languages
    included. A subclass names its models and columns; form and fields are
    derived from them."""

    extra = 0
    translation_model: type[Model]
    # Not fk_name: InlineModelAdmin uses that for the key to the product.
    translation_fk: str
    row_fields: tuple[str, ...] = ()
    translated_fields: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.form = translated_row_form(
            cls.translation_model, cls.translation_fk, cls.translated_fields
        )
        cls.fields = (
            *cls.row_fields,
            *(
                f"{name}_{locale}"
                for name in cls.translated_fields
                for locale in Locale.values
            ),
        )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("translations")


class ProductSpecInline(TranslatedRowInline):
    model = ProductSpec
    translation_model = ProductSpecTranslation
    translation_fk = "spec"
    row_fields = ("sort_order", "key")
    translated_fields = ("label", "value")


class ProductFaqInline(TranslatedRowInline):
    model = ProductFaq
    translation_model = ProductFaqTranslation
    translation_fk = "faq"
    row_fields = ("sort_order",)
    translated_fields = ("question", "answer")


class ProductProsConInline(TranslatedRowInline):
    model = ProductProsCon
    translation_model = ProductProsConTranslation
    translation_fk = "pros_con"
    row_fields = ("sort_order", "type")
    translated_fields = ("text",)


class DataCategoryInline(TranslatedRowInline):
    model = DataCategory
    translation_model = DataCategoryTranslation
    translation_fk = "data_category"
    row_fields = (
        "sort_order",
        "data_type",
        "server_region",
        "is_optional",
        "third_party_sharing",
    )
    translated_fields = ("notes",)


class ProductImageInline(TranslatedRowInline):
    """key is the relative path inside the media bucket ("products/x/hero.webp").
    The file itself lives on R2; the database never holds an absolute URL."""

    model = ProductImage
    translation_model = ProductImageTranslation
    translation_fk = "image"
    row_fields = ("sort_order", "key", "source", "is_primary")
    translated_fields = ("alt_text", "caption", "license_note")


class LinkedTranslationForm(forms.ModelForm):
    """A product's link to a shared row - a badge, a sub-category, a learning
    badge - together with that row's translations.

    The translations belong to the shared row, so a name corrected here is
    corrected on every product that carries it. An empty field keeps the
    stored text: a newly linked row shows its texts only after saving. It is
    refused only where the row has no text yet in a required column. The
    concrete form per link is built by linked_translation_form().
    """

    target_fk: str
    translation_model: type[Model]
    translation_fk: str
    translated_fields: tuple[str, ...]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            target = getattr(self.instance, self.target_fk)
            for translation in target.translations.all():
                for name in self.translated_fields:
                    self.initial.setdefault(
                        f"{name}_{translation.locale}", getattr(translation, name)
                    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        target = cleaned.get(self.target_fk)
        if target is None or cleaned.get("DELETE"):
            return cleaned
        stored = {t.locale: t for t in target.translations.all()}
        for name in self.translated_fields:
            column = self.translation_model._meta.get_field(name)
            for locale in Locale.values:
                key = f"{name}_{locale}"
                value = (cleaned.get(key) or "").strip()
                has_stored_text = bool(getattr(stored.get(locale), name, ""))
                if not value and not column.blank and not has_stored_text:
                    self.add_error(key, f"{target} has no {name} ({locale}) yet.")
                if value and name == "slug":
                    taken = (
                        self.translation_model.objects.filter(locale=locale, slug=value)
                        .exclude(**{self.translation_fk: target})
                        .exists()
                    )
                    if taken:
                        self.add_error(key, f"Another row already uses {value!r}.")
        return cleaned

    def _values_to_write(self) -> dict[str, dict[str, str]]:
        """Only the texts that were entered. When the link was switched to
        another row, the texts still on the form belong to the old one and
        are left out."""
        switched = bool(self.instance.pk) and self.target_fk in self.changed_data
        values: dict[str, dict[str, str]] = {}
        for locale in Locale.values:
            for name in self.translated_fields:
                key = f"{name}_{locale}"
                value = (self.cleaned_data.get(key) or "").strip()
                if value and not (switched and value == self.initial.get(key)):
                    values.setdefault(locale, {})[name] = value
        return values

    def save(self, commit: bool = True) -> Model:
        link = super().save(commit=commit)

        def save_translations() -> None:
            services.save_row_translations(
                getattr(link, self.target_fk),
                self.translation_model,
                self.translation_fk,
                self._values_to_write(),
            )

        if commit:
            save_translations()
            return link
        save_m2m = self.save_m2m

        def save_m2m_and_translations() -> None:
            save_m2m()
            save_translations()

        self.save_m2m = save_m2m_and_translations
        return link


class LinkedTranslationInline(admin.TabularInline):
    """The rows a product links to, each with its texts in every language.
    A subclass names the link table, the linked row and its translated
    columns; form and fields are derived from them."""

    extra = 0
    target_fk: str
    translation_model: type[Model]
    translation_fk: str
    translated_fields: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        attrs: dict[str, Any] = {
            "target_fk": cls.target_fk,
            "translation_model": cls.translation_model,
            "translation_fk": cls.translation_fk,
            "translated_fields": cls.translated_fields,
        }
        for name in cls.translated_fields:
            model_field = cls.translation_model._meta.get_field(name)
            for locale in Locale.values:
                form_field = model_field.formfield(required=False)
                if isinstance(form_field.widget, forms.Textarea):
                    form_field.widget.attrs.update(rows=2, cols=40)
                form_field.label = f"{model_field.verbose_name} ({locale})"
                attrs[f"{name}_{locale}"] = form_field
        cls.form = type(
            f"{cls.translation_model.__name__}LinkForm", (LinkedTranslationForm,), attrs
        )
        cls.fields = (
            cls.target_fk,
            *(
                f"{name}_{locale}"
                for name in cls.translated_fields
                for locale in Locale.values
            ),
        )


class ProductSubCategoryInline(LinkedTranslationInline):
    model = Product.sub_categories.through
    verbose_name = "sub-category"
    verbose_name_plural = (
        "sub-categories (shared: a text changed here changes it everywhere)"
    )
    target_fk = "subcategory"
    translation_model = SubCategoryTranslation
    translation_fk = "sub_category"
    translated_fields = ("name", "slug")


class ProductBadgeInline(LinkedTranslationInline):
    model = Product.badges.through
    verbose_name = "badge"
    verbose_name_plural = "badges (shared: a text changed here changes it everywhere)"
    target_fk = "badge"
    translation_model = BadgeTranslation
    translation_fk = "badge"
    translated_fields = ("name", "description")


class ProductLearningBadgeInline(LinkedTranslationInline):
    model = ToysProduct.learning_badges.through
    verbose_name = "learning badge"
    verbose_name_plural = (
        "learning badges (shared: a text changed here changes it everywhere)"
    )
    target_fk = "learningbadge"
    translation_model = LearningBadgeTranslation
    translation_fk = "learning_badge"
    translated_fields = ("name",)


class ProductAdminForm(forms.ModelForm):
    """Ticking is_published is the same act as the publish action. Its check
    reads the rows the inlines save, and those are saved after this form -
    so a first publication is only noted here and carried out in
    ProductAdminBase.save_related. Until then the product is saved unpublished,
    which also keeps the database constraint (a published product needs a
    score and a date) from refusing the form."""

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        # self.instance still holds the stored values here; the form's are
        # written into it only after clean().
        self.publish_after_inlines = bool(
            cleaned.get("is_published") and not self.instance.is_published
        )
        if self.publish_after_inlines:
            cleaned["is_published"] = False
        return cleaned


class ProductAdminBase(admin.ModelAdmin):
    """What the page of every product group shares - registered only through
    its subclasses, one per group.

    Review and release. Every visible text is editable on this page: the
    product's own texts, its rows with both languages side by side, and the
    shared rows it links to. Author, score and country open their own form
    from the pencil next to the field. The main category is fixed by the
    group.

    Ticking is_published is the same act as the publish action and goes
    through the same check - after the inlines are saved, so a text filled
    in on the same save counts."""

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
    # The three the database fills itself, and the main category, which the
    # group decides (Product.save). Everything else is editable here.
    readonly_fields = ("id", "created_at", "updated_at", "primary_category")
    # 249 countries are a search box, not a select.
    autocomplete_fields = ("manufactured_in_country",)
    # The group's own columns, as one more block of the form.
    group_fieldset: tuple = ()
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
                )
            },
        ),
        (
            "Classification",
            {
                "fields": ("primary_category",),
                "description": (
                    "The main category is the breadcrumb and the canonical "
                    "home, fixed by the product group. Sub-categories and "
                    "badges are edited in their tables below the texts."
                ),
            },
        ),
        (
            "Publication",
            {
                "fields": ("is_published", "published_at", "last_verified_at"),
                "description": (
                    "Publishing needs a traffic light score and every text "
                    "the page shows in German and English, none of it still "
                    "the analysis placeholder. What is missing is listed "
                    "after saving. Empty dates are filled on publication."
                ),
            },
        ),
        ("Meta", {"fields": ("analysis", "id", "created_at", "updated_at")}),
    )
    # Group inlines: the group's texts right after the product texts, its
    # own lists after the shared ones.
    group_text_inline: type[admin.StackedInline] | None = None
    group_list_inlines: tuple = ()
    actions = ("publish", "unpublish")

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        if self.group_fieldset:
            fieldsets.insert(3, self.group_fieldset)
        return fieldsets

    def get_inlines(self, request, obj):
        return (
            ProductTranslationInline,
            *((self.group_text_inline,) if self.group_text_inline else ()),
            ProductSpecInline,
            ProductFaqInline,
            ProductProsConInline,
            *self.group_list_inlines,
            ProductSubCategoryInline,
            ProductBadgeInline,
            ProductSourceInline,
            ProductImageInline,
            AffiliateLinkInline,
        )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        product = form.instance
        if getattr(form, "publish_after_inlines", False):
            try:
                services.publish_product(product)
            except services.NotPublishable as error:
                self.message_user(
                    request,
                    problem_list(
                        "Saved, but not published. Still missing:", error.problems
                    ),
                    level=messages.ERROR,
                )
        elif product.is_published:
            problems = services.publishing_problems(product, product.ampel_score)
            if problems:
                self.message_user(
                    request,
                    problem_list("Published, but no longer complete:", problems),
                    level=messages.WARNING,
                )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("brand", "author", "primary_category", "ampel_score")
            .prefetch_related(
                "translations",
                "primary_category__translations",
                "ampel_score__translations",
            )
        )

    @admin.action(description="Publish")
    def publish(self, request, queryset):
        published = 0
        for product in queryset:
            try:
                services.publish_product(product)
            except services.NotPublishable as error:
                self.message_user(
                    request,
                    problem_list(
                        f"{product} not published. Still missing:", error.problems
                    ),
                    level=messages.ERROR,
                )
                continue
            published += 1
        if published:
            self.message_user(request, f"{published} product(s) published.")

    @admin.action(description="Withdraw")
    def unpublish(self, request, queryset):
        for product in queryset:
            services.unpublish_product(product)
        self.message_user(request, f"{queryset.count()} product(s) withdrawn.")


CHILD_CERTIFIED_FIELDSET = (
    "Child safety",
    {"fields": ("is_child_certified",)},
)


@admin.register(ToysProduct)
class ToysProductAdmin(ProductAdminBase):
    group_fieldset = CHILD_CERTIFIED_FIELDSET
    group_text_inline = ToysTextInline
    group_list_inlines = (ProductLearningBadgeInline,)


@admin.register(SchoolProduct)
class SchoolProductAdmin(ProductAdminBase):
    group_fieldset = CHILD_CERTIFIED_FIELDSET
    group_text_inline = SchoolTextInline


@admin.register(TechProduct)
class TechProductAdmin(ProductAdminBase):
    group_fieldset = (
        "Data protection",
        {"fields": ("is_offline_capable", "requires_account")},
    )
    group_text_inline = TechTextInline
    group_list_inlines = (DataCategoryInline,)


@admin.register(Product)
class ProductOverviewAdmin(ProductAdminBase):
    """Every product of every group in one list, with the publish actions. A
    product is edited on the page of its group, where its group's columns,
    texts and lists are: opening one here leads there. New products are
    added in their group."""

    def has_add_permission(self, request) -> bool:
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        product = self.get_object(request, object_id)
        if product is None:
            return super().change_view(request, object_id, form_url, extra_context)
        group = selectors.concrete_product(product)
        return redirect(
            f"admin:{group._meta.app_label}_{group._meta.model_name}_change",
            object_id,
        )


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


class AuthorTranslationInline(BothLocalesInline, admin.StackedInline):
    model = AuthorTranslation
    fields = ("locale", "role", "bio", "credentials")


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    """The person who signs a published analysis - schema.org/Person, the
    E-E-A-T signal. same_as holds profile URLs as a JSON list; role, bio and
    credentials are entered once per language."""

    list_display = ("name", "slug")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    fields = ("name", "slug", "photo_key", "same_as")
    inlines = (AuthorTranslationInline,)


class BadgeTranslationInline(BothLocalesInline, admin.TabularInline):
    model = BadgeTranslation
    fields = ("locale", "name", "description")


@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    """Test marks and properties (CE, GS, FSC, ...)."""

    list_display = ("slug", "sort_order")
    ordering = ("sort_order", "slug")
    fields = ("slug", "sort_order")
    inlines = (BadgeTranslationInline,)


class MainCategoryTranslationInline(BothLocalesInline, admin.TabularInline):
    model = MainCategoryTranslation
    fields = ("locale", "name", "slug")


class MachineValueGuard:
    """Guards the machine value of a list row - what the pipeline writes and
    the API filters on, and for a main category the name of its prompt file.

    It is set once: renaming it would orphan every reference to it. The
    values the code refers to by name (`protected_values`) cannot be deleted
    here either, not even through the bulk action - without them every
    analysis would fail or lose its placeholders.
    """

    fixed_field = "slug"
    protected_values: tuple = ()

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        return (*fields, self.fixed_field) if obj else fields

    def has_delete_permission(self, request, obj=None):
        if obj is not None and getattr(obj, self.fixed_field) in self.protected_values:
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        lookup = {f"{self.fixed_field}__in": self.protected_values}
        kept = list(queryset.filter(**lookup).values_list(self.fixed_field, flat=True))
        if kept:
            self.message_user(
                request,
                f"Not deleted, the code refers to them: {', '.join(map(str, kept))}.",
                level=messages.WARNING,
            )
        super().delete_queryset(request, queryset.exclude(**lookup))


@admin.register(MainCategory)
class MainCategoryAdmin(MachineValueGuard, admin.ModelAdmin):
    """The three product groups. A new one also needs a prompt file named
    like its slug and an answer shape in pipeline_shared/analysis/schema/."""

    protected_values = tuple(schema.ANALYSIS_MODELS)
    list_display = ("__str__", "slug", "sort_order")
    fields = ("slug", "sort_order")
    inlines = (MainCategoryTranslationInline,)


class ChoiceListAdmin(MachineValueGuard, admin.ModelAdmin):
    """A choice list: the slug is the machine value, the labels per language
    are what a visitor reads."""

    list_display = ("__str__", "slug", "sort_order")
    fields = ("slug", "sort_order")


def label_inline(translation_model) -> type[admin.TabularInline]:
    """The label rows of a choice list, one per language."""
    return type(
        f"{translation_model.__name__}Inline",
        (BothLocalesInline, admin.TabularInline),
        {"model": translation_model, "fields": ("locale", "label")},
    )


# The slug lists all look alike: machine value, order, a label per language.
# The third column names the slugs the code refers to.
for choice_list, choice_translation, protected in (
    (DataType, DataTypeTranslation, ()),
    (ServerRegion, ServerRegionTranslation, (schema.UNKNOWN_SERVER_REGION,)),
    (Availability, AvailabilityTranslation, ()),
    (SourceType, SourceTypeTranslation, (YOUTUBE_SOURCE_TYPE,)),
    (ProsConType, ProsConTypeTranslation, schema.PLACEHOLDER_PROS_CON_TYPES),
):
    admin.site.register(
        choice_list,
        type(
            f"{choice_list.__name__}Admin",
            (ChoiceListAdmin,),
            {
                "inlines": (label_inline(choice_translation),),
                "protected_values": protected,
            },
        ),
    )


@admin.register(AmpelScore)
class AmpelScoreAdmin(MachineValueGuard, admin.ModelAdmin):
    fixed_field = "value"
    # The analysis writes 1 to 3, and the database allows nothing else.
    protected_values = (1, 2, 3)
    list_display = ("__str__", "value")
    fields = ("value",)
    inlines = (label_inline(AmpelScoreTranslation),)


@admin.register(Country)
class CountryAdmin(MachineValueGuard, admin.ModelAdmin):
    """Seeded from pycountry. search_fields serve the product form's country
    search as well."""

    fixed_field = "code"
    list_display = ("__str__", "code")
    search_fields = ("code", "translations__label")
    fields = ("code",)
    inlines = (label_inline(CountryTranslation),)


class LearningBadgeTranslationInline(BothLocalesInline, admin.TabularInline):
    model = LearningBadgeTranslation
    fields = ("locale", "name")


@admin.register(LearningBadge)
class LearningBadgeAdmin(admin.ModelAdmin):
    """Areas of development: coordination, logic, creativity, language,
    fine-motor, social-emotional, concentration."""

    list_display = ("slug", "sort_order")
    ordering = ("sort_order", "slug")
    fields = ("slug", "sort_order")
    inlines = (LearningBadgeTranslationInline,)
