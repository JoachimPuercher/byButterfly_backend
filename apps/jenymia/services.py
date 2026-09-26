"""Write access to jenymia data.

Everything that changes a row goes through here: the admin, the pipeline and
later the API all call the same functions, so a business rule exists exactly
once. The pipeline itself (download, transcribe, parse, prompt) lives in
pipeline_shared/ and the backend packages and knows nothing about the database beyond these calls.
"""

import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from .models import (
    YOUTUBE_SOURCE_TYPE,
    AmpelScore,
    Badge,
    BadgeTranslation,
    Brand,
    Country,
    DataCategory,
    DataCategoryTranslation,
    DataType,
    ExtractStatus,
    LearningBadge,
    LearningBadgeTranslation,
    Locale,
    Product,
    ProductFaq,
    ProductFaqTranslation,
    ProductProsCon,
    ProductProsConTranslation,
    ProductSource,
    ProductSpec,
    ProductSpecTranslation,
    ProductToAnalyse,
    ProductTranslation,
    ProsConType,
    ServerRegion,
    SourceType,
    SubCategory,
    SubCategoryTranslation,
    product_group,
)
from .pipeline_shared.analysis.schema import MISSING_DATA
from .selectors import concrete_product, product_slug_taken

logger = logging.getLogger(__name__)

# Backoff between retries of a failed ingest job, in seconds, first retry
# first. rq walks this list from the front only when its length equals the
# retry count, so request_analysis cuts it to INGEST_MAX_ATTEMPTS - 1.
RETRY_INTERVALS_SECONDS = [60, 300, 900, 1800, 3600]


# --- analysis order -------------------------------------------------------


def request_analysis(order: ProductToAnalyse) -> None:
    """Put an order into the queue.

    This is the only place that knows whether a broker exists. Without one
    (Railway in phase 1) the order simply stays queued until a machine with
    a worker picks it up - nothing is lost, nothing runs in the web process.
    """
    print("SERVICES.REQUEST_ANALYSIS - STARTED", order.pk)
    # attempts counts the runs of one queueing, not the lifetime of the order.
    # Without the reset a re-queued order starts at the old count and
    # run_ingest declares it exhausted on its first try.
    ProductToAnalyse.objects.filter(pk=order.pk).update(
        status=ProductToAnalyse.Status.QUEUED, error="", attempts=0
    )
    order.attempts = 0
    if not settings.DEPLOY_BACKGROUND_WORKERS:
        logger.info("No worker deployed, order %s stays queued.", order.pk)
        print("SERVICES.REQUEST_ANALYSIS - DONE", order.pk)
        return

    from rq import Retry

    from .pipeline_shared.ingest import run_ingest

    # Retry(max=N) means N re-runs after the first, so the total number of
    # executions is INGEST_MAX_ATTEMPTS - the same number run_ingest counts.
    retries = settings.INGEST_MAX_ATTEMPTS - 1
    _enqueue(
        order,
        run_ingest,
        retry=Retry(max=retries, interval=RETRY_INTERVALS_SECONDS[:retries]),
    )
    print("SERVICES.REQUEST_ANALYSIS - DONE", order.pk)


def request_extract(order: ProductToAnalyse) -> None:
    """Queue the second job. Kept separate from the first so the analysis
    can be repeated without downloading and transcribing everything again -
    the raw text is already in the database."""
    print("SERVICES.REQUEST_EXTRACT - STARTED", order.pk)
    if not settings.DEPLOY_BACKGROUND_WORKERS:
        logger.info("No worker deployed, order %s stays at text_extracted.", order.pk)
        print("SERVICES.REQUEST_EXTRACT - DONE", order.pk)
        return

    from .pipeline_shared.analysis.extract import run_extract

    _enqueue(order, run_extract)
    print("SERVICES.REQUEST_EXTRACT - DONE", order.pk)


def _enqueue(order: ProductToAnalyse, job, **options: Any) -> None:
    """Enqueue after the surrounding transaction commits. The admin saves
    inside a transaction, and a worker on a healthy Redis would otherwise
    fetch the job before the row exists."""
    print("SERVICES._ENQUEUE - STARTED", order.pk)
    import django_rq

    queue = django_rq.get_queue("default")
    transaction.on_commit(lambda: queue.enqueue(job, str(order.pk), **options))
    logger.info("Enqueued %s for order %s.", job.__name__, order.pk)
    print("SERVICES._ENQUEUE - DONE", order.pk)


def set_order_status(
    order: ProductToAnalyse,
    status: str,
    *,
    error: str = "",
    prompt_version: str = "",
    llm_provider: str = "",
    llm_model: str = "",
) -> None:
    print("SERVICES.SET_ORDER_STATUS - STARTED", status)
    fields: dict[str, Any] = {"status": status, "error": error}
    if status == ProductToAnalyse.Status.RUNNING:
        fields["attempts"] = order.attempts + 1
    if status == ProductToAnalyse.Status.ANALYSE_COMPLETE:
        fields["prompt_version"] = prompt_version[:60]
        # Truncated, not risked: this UPDATE runs after the product has been
        # committed, so a DataError here would leave the order on "running"
        # with a finished product next to it.
        fields["llm_provider"] = llm_provider[:20]
        fields["llm_model"] = llm_model[:60]
        fields["last_analysed_at"] = timezone.now()
    ProductToAnalyse.objects.filter(pk=order.pk).update(**fields)
    print("SERVICES.SET_ORDER_STATUS - DONE", status)


def _without_nul(value: Any) -> Any:
    """PostgreSQL refuses a NUL byte in text and in jsonb alike, and scraped
    pages, PDF fonts and video descriptions all produce them.

    Stripped here, at the one place every source writes through, rather than
    per field: a miss would surface as a DataError during the insert, which
    ingest cannot tell from a network failure and would retry five times.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_without_nul(item) for item in value]
    if isinstance(value, dict):
        return {key: _without_nul(item) for key, item in value.items()}
    return value


def save_source_text(source, raw_text: str, **metadata: Any) -> None:
    """Store the extracted text and the metadata of one source.

    Called for YoutubeUrl and WebUrl alike - both carry the same columns for
    the result, only their metadata columns differ.
    """
    print("SERVICES.SAVE_SOURCE_TEXT - STARTED", source.url)
    for field, value in metadata.items():
        setattr(source, field, _without_nul(value))
    source.raw_text = _without_nul(raw_text)
    source.extract_status = ExtractStatus.EXTRACTED
    source.error = ""
    source.save()
    print("SERVICES.SAVE_SOURCE_TEXT - DONE", source.url)


def save_source_error(source, error: str) -> None:
    print("SERVICES.SAVE_SOURCE_ERROR - STARTED", source.url)
    source.extract_status = ExtractStatus.FAILED
    source.error = error
    source.save(update_fields=["extract_status", "error", "updated_at"])
    print("SERVICES.SAVE_SOURCE_ERROR - DONE", source.url)


# --- product --------------------------------------------------------------


@transaction.atomic
def create_product_from_analysis(
    order: ProductToAnalyse, data: dict[str, Any]
) -> Product:
    """Turn the analysis result into rows.

    The shape of `data` is defined in pipeline_shared/analysis/schema/ - that and the
    prompt are the two places to change when the output changes. Nothing
    from the answer reaches a model constructor unfiltered: every dict goes
    through _clean(), which drops keys that are not columns and cuts strings
    to the column length.

    The product is written unpublished: is_published stays False until it
    has been edited by hand in the admin. AI text that goes online unedited
    is bad for SEO and worse for trust.
    """
    print("SERVICES.CREATE_PRODUCT_FROM_ANALYSIS - STARTED", order.pk)
    _replace_previous_product(order)

    brand_name = (data.get("brand") or "").strip()
    if not brand_name:
        raise ValueError("Analysis result has no brand.")
    brand_slug = slugify(brand_name)[: Brand._meta.get_field("slug").max_length].strip(
        "-_"
    )
    if not brand_slug:
        # Non-Latin scripts reduce to nothing. An empty slug is unique, so
        # the first such brand would be created and the second would collide.
        raise ValueError(f"Cannot build a slug from the brand {brand_name!r}.")
    brand, _ = Brand.objects.get_or_create(
        slug=brand_slug, defaults={"name": brand_name}
    )

    # The main category chosen on the order decided the prompt and the
    # answer shape, and it decides the product group - one class per main
    # category, each with its own columns.
    group = product_group(order.primary_category.slug)
    product = group.objects.create(
        brand=brand,
        analysis=order,
        primary_category=order.primary_category,
        is_published=False,
        # The group's own columns. The answer shape of the group has every
        # one of them (schema.ANALYSIS_MODELS), so a missing key is a bug and
        # raises instead of falling back to the column default.
        **{
            field.name: data[field.name]
            for field in group._meta.local_concrete_fields
            if not field.primary_key
        },
        **_clean(
            Product,
            {
                "model_name": data.get("model_name", ""),
                "gtin": data.get("gtin", ""),
                "ampel_score": _list_row(AmpelScore, "value", data.get("ampel_score")),
                "price_official": data.get("price_official"),
                # Set here and not by the model: a language model does not
                # reliably know today's date.
                # "is not None", not truthiness: a free product has the
                # price 0.00, which is falsy, and its price was checked too.
                "price_checked_at": (
                    timezone.now().date()
                    if data.get("price_official") is not None
                    else None
                ),
                "age_min_months": data.get("age_min_months"),
                "age_max_months": data.get("age_max_months"),
                "usage_lifespan_months": data.get("usage_lifespan_months"),
                "manufactured_in_country": _list_row(
                    Country, "code", data.get("manufactured_in_country")
                ),
            },
        ),
    )

    # The main category comes from the order (above); the analysis never
    # chooses it. Sub-categories, badges and learning badges come out of the
    # analysis and are created if they are new - everything the analysis
    # proposes is reviewed in the admin before the product goes public.
    product.sub_categories.set(_resolve_subcategories(data.get("sub_categories", [])))
    product.badges.set(
        _resolve_lookup(Badge, BadgeTranslation, "badge", data.get("badges", []))
    )
    if hasattr(product, "learning_badges"):
        product.learning_badges.set(
            _resolve_lookup(
                LearningBadge,
                LearningBadgeTranslation,
                "learning_badge",
                data["learning_badges"],
            )
        )

    # One text block per language in the answer; its shared texts go to
    # ProductTranslation, the group's own ones (safety, privacy, growth) to
    # the group's translation table.
    group_translation = group._meta.get_field("group_translations").related_model
    for locale, fields in data["translations"].items():
        shared, own = _split_text_block(fields, group_translation)
        ProductTranslation.objects.create(
            product=product,
            locale=locale,
            slug=_free_product_slug(
                locale, fields["title"], brand.name, product.model_name
            ),
            **_clean(ProductTranslation, shared, "slug"),
        )
        group_translation.objects.create(
            product=product, locale=locale, **_clean(group_translation, own)
        )

    _create_sources(product, order)
    _create_with_translations(
        ProductSpec, ProductSpecTranslation, "spec", product, data.get("specs", [])
    )
    _create_with_translations(
        ProductFaq, ProductFaqTranslation, "faq", product, data.get("faqs", [])
    )
    _create_with_translations(
        ProductProsCon,
        ProductProsConTranslation,
        "pros_con",
        product,
        _resolve_list_values(data.get("pros_cons", []), required={"type": ProsConType}),
    )
    if hasattr(product, "data_categories"):
        _create_with_translations(
            DataCategory,
            DataCategoryTranslation,
            "data_category",
            product,
            _resolve_list_values(
                data["data_categories"],
                required={"data_type": DataType},
                with_fallback={"server_region": (ServerRegion, "unknown")},
            ),
        )

    print("SERVICES.CREATE_PRODUCT_FROM_ANALYSIS - DONE", product.pk)
    return product


def _split_text_block(
    fields: dict[str, Any], group_translation: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(texts for ProductTranslation, texts for the group's translation
    table), each key going to the table that has its column. A key neither
    table has is logged once and dropped."""
    shared_columns = {f.name for f in ProductTranslation._meta.concrete_fields}
    own_columns = {f.name for f in group_translation._meta.concrete_fields}
    shared, own = {}, {}
    for key, value in fields.items():
        if key in shared_columns:
            shared[key] = value
        elif key in own_columns:
            own[key] = value
        else:
            logger.warning("Dropping unknown text %r from analysis output", key)
    return shared, own


def _free_product_slug(locale: str, title: str, brand: str, model_name: str) -> str:
    """The URL slug of a product in one language.

    The title alone, which is the translated one for every language. Only
    where another product already has that slug is something appended: the
    brand, then the model, then a number. Never an abort - the slug stays
    editable in the admin, and a title corrected later does not move it.
    The brand always yields a slug (checked before), so neither does a title
    in a script slugify cannot transliterate leave it empty.
    """
    limit = ProductTranslation._meta.get_field("slug").max_length

    def build(*parts: str) -> str:
        return slugify(" ".join(p for p in parts if p))[:limit].strip("-_")

    candidates = [build(title), build(title, brand), build(title, brand, model_name)]
    for candidate in dict.fromkeys(c for c in candidates if c):
        if not product_slug_taken(locale, candidate):
            return candidate
    base = next(c for c in reversed(candidates) if c)
    number = 2
    while True:
        suffix = f"-{number}"
        candidate = f"{base[: limit - len(suffix)]}{suffix}"
        if not product_slug_taken(locale, candidate):
            return candidate
        number += 1


class NotPublishable(ValueError):
    """The product is not ready to go public; `problems` names every gap."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


# Text a translation row may still hold from the analysis: the write step
# puts it where the answer had nothing, for a person to replace.
PLACEHOLDERS = frozenset(MISSING_DATA.values())
TEXT_TYPES = ("CharField", "TextField", "SlugField")


def check_publishable(product: Product, ampel_score: AmpelScore | None) -> None:
    """Raise NotPublishable unless the product may go public.

    The rules for going live exist exactly once, and both ways of publishing
    use them: the admin action and the checkbox on the product form. The
    database guards the score from the other side
    (product_published_requires_score_and_date), but a refusal here names
    what is missing, while the constraint only reports a failed insert.

    The score is passed separately because the admin checks a value the
    product may not carry yet.
    """
    problems = publishing_problems(product, ampel_score)
    if problems:
        raise NotPublishable(problems)


def publishing_problems(product: Product, ampel_score: AmpelScore | None) -> list[str]:
    """Everything that keeps the product from going public, one line each.

    Every word the page shows comes from the database in both languages, so
    every row the page reads is checked: the product's own texts, its specs,
    FAQs, pros and cons, data categories and images, the sub-categories,
    badges, learning badges and author it points at, and the label of every
    choice list value it uses. A row needs a translation per language; a
    text filled in one language needs the other; a required column must not
    be empty; and no text may still be the placeholder of the analysis.
    """
    if product.pk is None:
        return ["Save the product first, then publish it."]
    problems = []
    if ampel_score is None:
        problems.append("The traffic light score (ampel_score) is missing.")
    for label, translations in _texts_the_page_shows(product):
        problems += _translation_gaps(label, translations)
    return problems


def _texts_the_page_shows(product: Product) -> list[tuple[str, list[Any]]]:
    """(label for the message, translation rows) for every translated row
    the product page reads, the group's own ones included. A choice list
    value used twice is listed once."""
    # The group's texts and lists live on the group's class; the admin's
    # overview and the publish action may hand in the bare Product.
    product = concrete_product(product)
    rows: list[tuple[str, Any]] = [
        (f"Spec '{spec.key}'", spec) for spec in product.specs.all()
    ]
    rows += [(f"FAQ {i}", faq) for i, faq in enumerate(product.faqs.all(), 1)]
    pros_cons = list(product.pros_cons.select_related("type"))
    rows += [(f"{p.type.slug} {i}", p) for i, p in enumerate(pros_cons, 1)]
    data_categories = (
        list(product.data_categories.select_related("data_type", "server_region"))
        if hasattr(product, "data_categories")
        else []
    )
    rows += [(f"Data category '{d.data_type.slug}'", d) for d in data_categories]
    rows += [(f"Image '{image.key}'", image) for image in product.images.all()]
    rows += [(f"Sub-category '{sub}'", sub) for sub in product.sub_categories.all()]
    rows += [(f"Badge '{badge.slug}'", badge) for badge in product.badges.all()]
    if hasattr(product, "learning_badges"):
        rows += [
            (f"Learning badge '{badge.slug}'", badge)
            for badge in product.learning_badges.all()
        ]
    if product.author_id:
        rows.append((f"Author '{product.author.name}'", product.author))

    choices = [product.primary_category, product.ampel_score]
    choices.append(product.manufactured_in_country)
    choices += [p.type for p in pros_cons]
    choices += [d.data_type for d in data_categories]
    choices += [d.server_region for d in data_categories]
    choices += [s.source_type for s in product.sources.select_related("source_type")]
    choices += [
        link.availability
        for link in product.affiliate_links.select_related("availability")
    ]
    seen = set()
    for row in choices:
        if row is None or (type(row), row.pk) in seen:
            continue
        seen.add((type(row), row.pk))
        rows.append((f"{type(row)._meta.verbose_name.capitalize()} '{row}'", row))
    group_name = type(product)._meta.verbose_name.capitalize()
    return [
        ("Product text", list(product.translations.all())),
        (f"{group_name} text", list(product.group_translations.all())),
        *((label, list(row.translations.all())) for label, row in rows),
    ]


def _translation_gaps(label: str, translation_rows: list[Any]) -> list[str]:
    translations = {t.locale: t for t in translation_rows}
    gaps = [
        f"{label}: the {locale} translation is missing."
        for locale in Locale.values
        if locale not in translations
    ]
    if not translations:
        return gaps
    model = type(next(iter(translations.values())))
    fields = [
        f
        for f in model._meta.concrete_fields
        if f.get_internal_type() in TEXT_TYPES and f.name != "locale"
    ]
    for field in fields:
        values = {
            locale: (getattr(t, field.name) or "").strip()
            for locale, t in translations.items()
        }
        for locale, value in values.items():
            if value in PLACEHOLDERS:
                gaps.append(
                    f"{label}: {field.name} ({locale}) is still the placeholder."
                )
            elif not value and (not field.blank or any(values.values())):
                gaps.append(f"{label}: {field.name} ({locale}) is empty.")
    return gaps


def publish_product(product: Product) -> None:
    """Make a product public.

    published_at is set on the first publication and never moved afterwards:
    it feeds schema.org datePublished and the sitemap. last_verified_at is the
    freshness signal and is renewed on every publication.
    """
    check_publishable(product, product.ampel_score)

    now = timezone.now()
    Product.objects.filter(pk=product.pk).update(
        is_published=True,
        published_at=product.published_at or now,
        last_verified_at=now,
        updated_at=now,
    )


def unpublish_product(product: Product) -> None:
    """Take a product off the site. published_at stays as it is - the page was
    published on that date, and a later re-publication does not change that."""
    Product.objects.filter(pk=product.pk).update(
        is_published=False, updated_at=timezone.now()
    )


def save_row_translations(
    row: Any,
    translation_model: Any,
    fk_name: str,
    values_by_locale: dict[str, dict[str, Any]],
) -> None:
    """Create or update the translations of one detail row - a spec, a FAQ,
    a pros/cons point - one row per locale.

    Used by the admin, which edits both languages directly in the product's
    inline instead of on a separate page per row.
    """
    for locale, values in values_by_locale.items():
        translation_model.objects.update_or_create(
            locale=locale, **{fk_name: row}, defaults=values
        )


def _replace_previous_product(order: ProductToAnalyse) -> None:
    """A re-run after a prompt fix replaces the draft it produced before.
    A product that is already public is never touched by the pipeline."""
    print("SERVICES._REPLACE_PREVIOUS_PRODUCT - STARTED", order.pk)
    previous = Product.objects.filter(analysis=order).first()
    if previous is None:
        print("SERVICES._REPLACE_PREVIOUS_PRODUCT - DONE", order.pk)
        return
    if previous.is_published:
        raise ValueError(
            f"Order {order.pk} already has a published product; unpublish it first."
        )
    previous.delete()
    print("SERVICES._REPLACE_PREVIOUS_PRODUCT - DONE", order.pk)


def _create_sources(product: Product, order: ProductToAnalyse) -> None:
    """The public citation list comes from the order's own URLs, with the
    metadata the fetchers stored - never from the analysis text, which could
    invent a source."""
    print("SERVICES._CREATE_SOURCES - STARTED", product.pk)
    extracted = list(order.youtube_urls.filter(extract_status=ExtractStatus.EXTRACTED))
    rows = []
    if extracted:
        youtube = SourceType.objects.filter(slug=YOUTUBE_SOURCE_TYPE).first()
        if youtube is None:
            raise ValueError(
                f"Source type {YOUTUBE_SOURCE_TYPE!r} is missing; "
                "restore it in the admin."
            )
        rows = [
            (src.title, src.webpage_url or src.url, youtube, src.source_date)
            for src in extracted
        ]
    extracted = order.web_urls.filter(
        extract_status=ExtractStatus.EXTRACTED
    ).select_related("source_type")
    rows += [
        (
            src.title,
            src.canonical_url or src.final_url or src.url,
            src.source_type,
            src.source_date,
        )
        for src in extracted
    ]
    for sort_order, (label, url, source_type, published_at) in enumerate(rows):
        ProductSource.objects.create(
            product=product,
            label=label,
            url=url,
            source_type=source_type,
            published_at=published_at,
            sort_order=sort_order,
        )
    print("SERVICES._CREATE_SOURCES - DONE", product.pk)


def _resolve_subcategories(entries: list[dict]) -> list[SubCategory]:
    """Reuse the sub-category whose slug already exists in any language,
    otherwise create it.

    A reused one that lacks a language gets it from the proposal, like a
    badge does in _resolve_lookup - a row entered by hand in German only
    would otherwise answer with null for /en/ forever. A name that exists is
    never overwritten.

    Flat: a sub-category belongs to no main category, which is what lets the
    same one sit on products of different groups.
    """
    print("SERVICES._RESOLVE_SUBCATEGORIES - STARTED", len(entries))
    categories = []
    for entry in entries:
        translations = entry.get("translations", {})
        # slugify can reduce a proposal to nothing (punctuation only, a script
        # it cannot transliterate). Every language needs one: an empty slug
        # would both create an unreachable /en// page and, worse, match the
        # next empty one and file an unrelated product under this category.
        slugs = {
            locale: fields.get("slug", "") for locale, fields in translations.items()
        }
        if not slugs or not all(slugs.values()):
            logger.warning(
                "Dropping sub-category proposal without a slug in every language."
            )
            continue
        existing = next(
            (
                t.sub_category
                for locale, slug in slugs.items()
                for t in SubCategoryTranslation.objects.filter(
                    locale=locale, slug=slug
                ).select_related("sub_category")
            ),
            None,
        )
        if existing:
            present = set(existing.translations.values_list("locale", flat=True))
            for locale, fields in translations.items():
                if locale in present:
                    continue
                # The slug is unique per language: if another sub-category
                # already owns it, the language stays missing - logged, and
                # left to the admin - instead of aborting the insert.
                if SubCategoryTranslation.objects.filter(
                    locale=locale, slug=fields.get("slug", "")
                ).exists():
                    logger.warning(
                        "Not adding %s to sub-category %s: slug %r is taken.",
                        locale,
                        existing.pk,
                        fields.get("slug"),
                    )
                    continue
                SubCategoryTranslation.objects.create(
                    sub_category=existing,
                    locale=locale,
                    **_clean(SubCategoryTranslation, fields),
                )
            categories.append(existing)
            continue

        category = SubCategory.objects.create()
        for locale, fields in translations.items():
            SubCategoryTranslation.objects.create(
                sub_category=category,
                locale=locale,
                **_clean(SubCategoryTranslation, fields),
            )
        categories.append(category)
    print("SERVICES._RESOLVE_SUBCATEGORIES - DONE", len(categories))
    return categories


def _resolve_lookup(
    model, translation_model, fk_name: str, entries: list[dict]
) -> list:
    """Create the badges or learning badges the analysis proposes, keep the
    ones that exist.

    A name that exists is never overwritten - a correction made by hand in
    the admin has to survive the next analysis. A language that is *missing*
    is filled in, though: a row seeded with a German name only would
    otherwise answer with null for /en/ forever.
    """
    print("SERVICES._RESOLVE_LOOKUP - STARTED", model.__name__)
    rows = []
    for entry in entries:
        if not entry.get("slug"):
            logger.warning(
                "Dropping %s proposal without a usable slug.", model.__name__
            )
            continue
        row, _ = model.objects.get_or_create(slug=entry["slug"])
        for locale, fields in entry.get("translations", {}).items():
            translation_model.objects.get_or_create(
                locale=locale,
                **{fk_name: row},
                defaults=_clean(translation_model, fields),
            )
        rows.append(row)
    print("SERVICES._RESOLVE_LOOKUP - DONE", model.__name__)
    return rows


def _create_with_translations(
    model, translation_model, fk_name: str, product: Product, entries: list[dict]
) -> None:
    """All detail tables have the same shape: a row on the product plus one
    translation row per locale. One function instead of four identical loops.
    """
    print("SERVICES._CREATE_WITH_TRANSLATIONS - STARTED", model.__name__)
    for entry in entries:
        # "translations" is handled here, so _clean must not report it as an
        # unknown column.
        row = model.objects.create(
            product=product, **_clean(model, entry, "translations")
        )
        for locale, fields in entry.get("translations", {}).items():
            translation_model.objects.create(
                locale=locale, **{fk_name: row}, **_clean(translation_model, fields)
            )
    print("SERVICES._CREATE_WITH_TRANSLATIONS - DONE", model.__name__)


def _list_row(model, lookup_field: str, value: Any) -> Any:
    """The row of a choice list that the analysis named, or None.

    Slugs are matched case-insensitively ("Pro" is "pro"), ISO codes in upper
    case. A value that is in no list is logged and becomes None: the lists
    in the database are the vocabulary, and a value outside it has no label
    to show.
    """
    if value in (None, ""):
        return None
    if lookup_field == "slug":
        lookup = {"slug__iexact": str(value).strip()}
    elif lookup_field == "code":
        lookup = {"code": str(value).strip().upper()}
    else:
        lookup = {lookup_field: value}
    row = model.objects.filter(**lookup).first()
    if row is None:
        logger.warning("Dropping %r: no such %s.", value, model.__name__)
    return row


def _resolve_list_values(
    entries: list[dict],
    required: dict[str, Any],
    with_fallback: dict[str, tuple[Any, str]] | None = None,
) -> list[dict]:
    """Replace the slugs in list entries with the rows they name.

    `required` maps a field to its list; an entry whose value is in no list
    is dropped, because the column cannot be empty. `with_fallback` maps a
    field to its list and the slug to use when the value is in none.
    """
    resolved = []
    for entry in entries:
        entry = dict(entry)
        usable = True
        for field, model in required.items():
            entry[field] = _list_row(model, "slug", entry.get(field))
            if entry[field] is None:
                logger.warning(
                    "Dropping %s entry without a usable %s.", model.__name__, field
                )
                usable = False
        for field, (model, fallback) in (with_fallback or {}).items():
            entry[field] = _list_row(model, "slug", entry.get(field)) or _list_row(
                model, "slug", fallback
            )
        if usable:
            resolved.append(entry)
    return resolved


def _clean(model, values: dict[str, Any], *handled_elsewhere: str) -> dict[str, Any]:
    """Keep only what the model can store.

    Drops keys that are not columns and cuts strings to the column length.
    The analysis output is text from a language model: an extra key or 80
    characters too many must not abort the whole product. A foreign key is a
    column too; its value is the row, resolved by the caller (_list_row).

    `handled_elsewhere` names keys this function must neither write nor
    complain about, because the caller deals with them itself - the slug it
    builds, the nested `translations` it writes into their own table. Without
    that distinction every detail row would log a false "unknown field".
    """
    written_here = {"id", "created_at", "updated_at", "locale", *handled_elsewhere}
    columns = {
        f.name: f
        for f in model._meta.concrete_fields
        if not f.is_relation or f.many_to_one
    }
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        if key in written_here:
            continue
        column = columns.get(key)
        if column is None:
            logger.warning(
                "Dropping unknown field %s.%s from analysis output", model.__name__, key
            )
            continue
        max_length = getattr(column, "max_length", None)
        if max_length and isinstance(value, str) and len(value) > max_length:
            logger.warning(
                "Cutting %s.%s from %s to %s characters",
                model.__name__,
                key,
                len(value),
                max_length,
            )
            value = value[:max_length]
        cleaned[key] = value
    return cleaned
