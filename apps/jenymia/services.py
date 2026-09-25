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
    Badge,
    BadgeTranslation,
    Brand,
    Category,
    CategoryTranslation,
    DataCategory,
    DataCategoryTranslation,
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
    SourceType,
)
from .selectors import product_slug_taken

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
    # attempts counts the runs of one queueing, not the lifetime of the order.
    # Without the reset a re-queued order starts at the old count and
    # run_ingest declares it exhausted on its first try.
    ProductToAnalyse.objects.filter(pk=order.pk).update(
        status=ProductToAnalyse.Status.QUEUED, error="", attempts=0
    )
    order.attempts = 0
    if not settings.DEPLOY_BACKGROUND_WORKERS:
        logger.info("No worker deployed, order %s stays queued.", order.pk)
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


def request_extract(order: ProductToAnalyse) -> None:
    """Queue the second job. Kept separate from the first so the analysis
    can be repeated without downloading and transcribing everything again -
    the raw text is already in the database."""
    if not settings.DEPLOY_BACKGROUND_WORKERS:
        logger.info("No worker deployed, order %s stays at text_extracted.", order.pk)
        return

    from .pipeline_shared.extract import run_extract

    _enqueue(order, run_extract)


def _enqueue(order: ProductToAnalyse, job, **options: Any) -> None:
    """Enqueue after the surrounding transaction commits. The admin saves
    inside a transaction, and a worker on a healthy Redis would otherwise
    fetch the job before the row exists."""
    import django_rq

    queue = django_rq.get_queue("default")
    transaction.on_commit(lambda: queue.enqueue(job, str(order.pk), **options))
    logger.info("Enqueued %s for order %s.", job.__name__, order.pk)


def set_order_status(
    order: ProductToAnalyse,
    status: str,
    *,
    error: str = "",
    prompt_version: str = "",
) -> None:
    fields: dict[str, Any] = {"status": status, "error": error}
    if status == ProductToAnalyse.Status.RUNNING:
        fields["attempts"] = order.attempts + 1
    if status == ProductToAnalyse.Status.ANALYSE_COMPLETE:
        fields["prompt_version"] = prompt_version
        fields["last_analysed_at"] = timezone.now()
    ProductToAnalyse.objects.filter(pk=order.pk).update(**fields)


def save_source_text(source, raw_text: str, **metadata: Any) -> None:
    """Store the extracted text and the metadata of one source.

    Called for YoutubeUrl and WebUrl alike - both carry the same columns for
    the result, only their metadata columns differ.
    """
    for field, value in metadata.items():
        setattr(source, field, value)
    source.raw_text = raw_text
    source.extract_status = ExtractStatus.EXTRACTED
    source.error = ""
    source.save()


def save_source_error(source, error: str) -> None:
    source.extract_status = ExtractStatus.FAILED
    source.error = error
    source.save(update_fields=["extract_status", "error", "updated_at"])


# --- product --------------------------------------------------------------


@transaction.atomic
def create_product_from_analysis(
    order: ProductToAnalyse, data: dict[str, Any]
) -> Product:
    """Turn the analysis result into rows.

    The shape of `data` is defined in pipeline_shared/schema.py - that file and the
    prompt are the two places to change when the output changes. Nothing
    from the answer reaches a model constructor unfiltered: every dict goes
    through _clean(), which drops keys that are not columns and cuts strings
    to the column length.

    The product is written unpublished: is_published stays False until it
    has been edited by hand in the admin. AI text that goes online unedited
    is bad for SEO and worse for trust.
    """
    _replace_previous_product(order)

    brand_name = (data.get("brand") or "").strip()
    if not brand_name:
        raise ValueError("Analysis result has no brand.")
    brand, _ = Brand.objects.get_or_create(
        slug=slugify(brand_name), defaults={"name": brand_name}
    )

    product = Product.objects.create(
        brand=brand,
        analysis=order,
        is_published=False,
        **_clean(
            Product,
            {
                "model_name": data.get("model_name", ""),
                "gtin": data.get("gtin", ""),
                "ampel_score": data.get("ampel_score"),
                "price_current": data.get("price_current"),
                "price_original": data.get("price_original"),
                "age_min_months": data.get("age_min_months"),
                "age_max_months": data.get("age_max_months"),
                "usage_lifespan_months": data.get("usage_lifespan_months"),
                "manufactured_in_country": data.get("manufactured_in_country", ""),
                "is_offline_capable": data.get("is_offline_capable", False),
                "requires_account": data.get("requires_account", False),
                "is_child_certified": data.get("is_child_certified", False),
            },
        ),
    )

    # The main category was chosen by hand on the order and decided which
    # prompt ran. Sub-categories and badges come out of the analysis and are
    # created here if they are new - everything the analysis proposes is
    # reviewed in the admin before the product goes public.
    product.categories.set(
        [
            order.category,
            *_resolve_subcategories(order.category, data.get("categories", [])),
        ]
    )
    product.badges.set(
        _resolve_lookup(Badge, BadgeTranslation, "badge", data.get("badges", []))
    )
    product.learning_badges.set(
        _resolve_lookup(
            LearningBadge,
            LearningBadgeTranslation,
            "learning_badge",
            data.get("learning_badges", []),
        )
    )

    for locale, fields in data["translations"].items():
        # Brand + title + model identifies a product on the market, so it
        # also identifies the page. The title carries neither of the other
        # two (see schema.py), otherwise the slug would repeat them.
        slug = slugify(f"{brand.name} {fields['title']} {product.model_name}")
        if product_slug_taken(locale, slug):
            # Two products with the same brand, title and model do not exist
            # on the market, so this is a duplicate order or a wrong title.
            raise ValueError(f"Slug '{slug}' already exists for locale '{locale}'.")
        ProductTranslation.objects.create(
            product=product,
            locale=locale,
            slug=slug,
            **_clean(ProductTranslation, fields, "slug"),
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
        data.get("pros_cons", []),
    )
    _create_with_translations(
        DataCategory,
        DataCategoryTranslation,
        "data_category",
        product,
        data.get("data_categories", []),
    )

    return product


def publish_product(product: Product) -> None:
    """Make a product public.

    The only place that writes is_published, published_at and
    last_verified_at, so the rules for going live exist exactly once. The
    database guards the same thing from the other side
    (product_published_requires_score_and_date), but a refusal here names what
    is missing, while the constraint only reports a failed insert.

    published_at is set on the first publication and never moved afterwards:
    it feeds schema.org datePublished and the sitemap. last_verified_at is the
    freshness signal and is renewed on every publication.
    """
    if product.ampel_score is None:
        raise ValueError("A product without an ampel_score cannot be published.")

    present = set(product.translations.values_list("locale", flat=True))
    missing = [locale for locale in Locale.values if locale not in present]
    if missing:
        raise ValueError(f"Missing translations for: {', '.join(missing)}.")

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


def _replace_previous_product(order: ProductToAnalyse) -> None:
    """A re-run after a prompt fix replaces the draft it produced before.
    A product that is already public is never touched by the pipeline."""
    previous = Product.objects.filter(analysis=order).first()
    if previous is None:
        return
    if previous.is_published:
        raise ValueError(
            f"Order {order.pk} already has a published product; unpublish it first."
        )
    previous.delete()


def _create_sources(product: Product, order: ProductToAnalyse) -> None:
    """The public citation list comes from the order's own URLs, with the
    metadata the fetchers stored - never from the analysis text, which could
    invent a source."""
    extracted = order.youtube_urls.filter(extract_status=ExtractStatus.EXTRACTED)
    rows = [
        (src.title, src.webpage_url or src.url, SourceType.YOUTUBE, src.source_date)
        for src in extracted
    ]
    extracted = order.web_urls.filter(extract_status=ExtractStatus.EXTRACTED)
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


def _resolve_subcategories(main: Category, entries: list[dict]) -> list[Category]:
    """Reuse the category whose slug already exists in any language,
    otherwise create it under the main category.

    A new category inherits the pipeline from its parent, so its own
    pipeline field stays empty.
    """
    categories = []
    for entry in entries:
        translations = entry.get("translations", {})
        existing = next(
            (
                t.category
                for locale, fields in translations.items()
                for t in CategoryTranslation.objects.filter(
                    locale=locale, slug=fields.get("slug", "")
                ).select_related("category")
            ),
            None,
        )
        if existing:
            categories.append(existing)
            continue

        category = Category.objects.create(parent=main)
        for locale, fields in translations.items():
            CategoryTranslation.objects.create(
                category=category, locale=locale, **_clean(CategoryTranslation, fields)
            )
        categories.append(category)
    return categories


def _resolve_lookup(model, translation_model, fk_name: str, entries: list[dict]) -> list:
    """Create the badges or learning badges the analysis proposes, keep the
    ones that exist.

    Translations of an existing row are left untouched: a name corrected by
    hand in the admin must not be overwritten by the next analysis.
    """
    rows = []
    for entry in entries:
        if not entry.get("slug"):
            continue
        row, created = model.objects.get_or_create(slug=entry["slug"])
        if created:
            for locale, fields in entry.get("translations", {}).items():
                translation_model.objects.create(
                    locale=locale, **{fk_name: row}, **_clean(translation_model, fields)
                )
        rows.append(row)
    return rows


def _create_with_translations(
    model, translation_model, fk_name: str, product: Product, entries: list[dict]
) -> None:
    """All detail tables have the same shape: a row on the product plus one
    translation row per locale. One function instead of four identical loops.
    """
    for entry in entries:
        row = model.objects.create(product=product, **_clean(model, entry))
        for locale, fields in entry.get("translations", {}).items():
            translation_model.objects.create(
                locale=locale, **{fk_name: row}, **_clean(translation_model, fields)
            )


def _clean(model, values: dict[str, Any], *set_by_us: str) -> dict[str, Any]:
    """Keep only what the model can store.

    Drops keys that are not plain columns (unknown names, relations, the
    BaseModel fields, `locale`, and whatever the caller sets itself) and cuts
    strings to the column length. The analysis output is text from a
    language model: an extra key or 80 characters too many must not abort
    the whole product.
    """
    protected = {"id", "created_at", "updated_at", "locale", *set_by_us}
    columns = {
        f.name: f
        for f in model._meta.concrete_fields
        if not f.is_relation and f.name not in protected
    }
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
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
