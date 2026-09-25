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
    SubCategory,
    SubCategoryTranslation,
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
    llm_provider: str = "",
    llm_model: str = "",
) -> None:
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
    for field, value in metadata.items():
        setattr(source, field, _without_nul(value))
    source.raw_text = _without_nul(raw_text)
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

    product = Product.objects.create(
        brand=brand,
        analysis=order,
        # What was chosen on the order is what analysed it.
        primary_category=order.primary_category,
        is_published=False,
        **_clean(
            Product,
            {
                "model_name": data.get("model_name", ""),
                "gtin": data.get("gtin", ""),
                "ampel_score": data.get("ampel_score"),
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
                "manufactured_in_country": data.get("manufactured_in_country", ""),
                "is_offline_capable": data.get("is_offline_capable", False),
                "requires_account": data.get("requires_account", False),
                "is_child_certified": data.get("is_child_certified", False),
            },
        ),
    )

    # The main category is a value on the product, not a row, so only the
    # sub-categories go into a relation here. Sub-categories, badges and
    # learning badges come out of the analysis and are created if they are
    # new - everything the analysis proposes is reviewed in the admin before
    # the product goes public.
    product.sub_categories.set(_resolve_subcategories(data.get("sub_categories", [])))
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
        slug = slugify(f"{brand.name} {fields['title']} {product.model_name}")[
            : ProductTranslation._meta.get_field("slug").max_length
        ].strip("-_")
        if not slug:
            raise ValueError(
                f"Cannot build a slug for locale {locale!r} from brand "
                f"{brand.name!r} and title {fields['title']!r}."
            )
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


def check_publishable(product: Product, ampel_score: int | None) -> None:
    """Raise unless the product may go public.

    The rules for going live exist exactly once, and both ways of publishing
    use them: the admin action below and the checkbox on the product form.
    The database guards the same thing from the other side
    (product_published_requires_score_and_date), but a refusal here names what
    is missing, while the constraint only reports a failed insert.

    The score is passed separately because the admin form validates a value
    the product does not carry yet.
    """
    if product.pk is None:
        raise ValueError("Save the product first, then publish it.")
    if ampel_score is None:
        raise ValueError("A product without an ampel_score cannot be published.")

    present = set(product.translations.values_list("locale", flat=True))
    missing = [locale for locale in Locale.values if locale not in present]
    if missing:
        raise ValueError(f"Missing translations for: {', '.join(missing)}.")


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


def _resolve_subcategories(entries: list[dict]) -> list[SubCategory]:
    """Reuse the sub-category whose slug already exists in any language,
    otherwise create it.

    Flat: a sub-category belongs to no main category, which is what lets the
    same one sit on products of different groups.
    """
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
    return rows


def _create_with_translations(
    model, translation_model, fk_name: str, product: Product, entries: list[dict]
) -> None:
    """All detail tables have the same shape: a row on the product plus one
    translation row per locale. One function instead of four identical loops.
    """
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


def _clean(model, values: dict[str, Any], *handled_elsewhere: str) -> dict[str, Any]:
    """Keep only what the model can store.

    Drops keys that are not plain columns and cuts strings to the column
    length. The analysis output is text from a language model: an extra key
    or 80 characters too many must not abort the whole product.

    `handled_elsewhere` names keys this function must neither write nor
    complain about, because the caller deals with them itself - the slug it
    builds, the nested `translations` it writes into their own table. Without
    that distinction every detail row would log a false "unknown field".
    """
    written_here = {"id", "created_at", "updated_at", "locale", *handled_elsewhere}
    columns = {f.name: f for f in model._meta.concrete_fields if not f.is_relation}
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
