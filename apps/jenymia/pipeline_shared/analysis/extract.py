"""Job 2: turn the collected texts into one product.

Runs only after job 1 has produced text, and it can be repeated without
downloading anything again - the raw material stays in the database.

Two requests to Claude (claude.py): the analysis, written in German only,
then its English translation (translate.py). The expected answer is defined
once in schema.py, and every answer is validated before a row is written.
"""

import json
import logging

from apps.jenymia import selectors, services
from apps.jenymia.models import YOUTUBE_SOURCE_TYPE, ExtractStatus, ProductToAnalyse

from . import schema, translate
from .prompt_files import read_prompt

logger = logging.getLogger(__name__)

# The tool Claude calls with the finished German analysis.
ANALYSIS_TOOL = "save_analysis"


def run_extract(order_id: str) -> None:
    print("EXTRACT.RUN_EXTRACT - STARTED", order_id)
    order = ProductToAnalyse.objects.select_related(
        "sub_category", "primary_category"
    ).get(pk=order_id)

    # Everything that can fail is inside the try, including the prompt
    # files. An exception thrown before it would leave the order on
    # text_extracted with an empty error, which in the admin is
    # indistinguishable from a run that is still going.
    try:
        # The main category picks the prompt and the answer shape. A
        # sub-category may override the wording without changing the shape.
        pipeline = order.primary_category.slug
        prompt_name = (
            order.sub_category.prompt_name if order.sub_category_id else ""
        ) or pipeline

        sources = _collect_sources(order)
        if not sources:
            services.set_order_status(
                order,
                ProductToAnalyse.Status.FAILED,
                error="No source text to analyse.",
            )
            print("EXTRACT.RUN_EXTRACT - DONE", order_id)
            return

        # What the order already knows about the product. Video tests
        # regularly cover several devices, so without this the model has no
        # way of telling which passages are about this one - and brand is a
        # field the write step refuses to do without.
        product = {
            "title": order.title,
            "brand": order.brand,
            # German, like the prompt it goes into.
            "main_category": order.primary_category.name_in("de"),
        }
        # The values the answer may use for its choice fields, read from the
        # lists in the database - the same rows the write step resolves them
        # against.
        choices = selectors.analysis_choices()
        prompt, prompt_version = build_prompt(
            prompt_name, pipeline, product, sources, choices
        )
        german, provider, model = analyse(prompt, pipeline, choices)
        answer, translate_version = translate.translate(
            json.loads(german), pipeline, order_facts=product
        )
        prompt_version = f"{prompt_version}+{translate_version}"
        data = schema.parse(answer, pipeline, order_facts=product, choices=choices)
        product = services.create_product_from_analysis(order, data)
    except Exception as error:
        logger.exception("Analysis failed for order %s", order_id)
        services.set_order_status(
            order,
            ProductToAnalyse.Status.FAILED,
            error=f"{type(error).__name__}: {error}",
        )
        print("EXTRACT.RUN_EXTRACT - ERROR", order_id)
        return

    services.set_order_status(
        order,
        ProductToAnalyse.Status.ANALYSE_COMPLETE,
        prompt_version=prompt_version,
        llm_provider=provider,
        llm_model=model,
    )
    logger.info("Created product %s from order %s.", product.pk, order_id)
    print("EXTRACT.RUN_EXTRACT - DONE", order_id)


def _collect_sources(order: ProductToAnalyse) -> list[dict]:
    """Every source that produced text, with the metadata the model needs to
    weigh it: who said it, what kind of publication it is, and when."""
    print("EXTRACT._COLLECT_SOURCES - STARTED", order.pk)
    sources = []
    for source in order.youtube_urls.filter(extract_status=ExtractStatus.EXTRACTED):
        sources.append(
            {
                "type": YOUTUBE_SOURCE_TYPE,
                "url": source.webpage_url or source.url,
                "label": source.title,
                "publisher": source.channel,
                "published_at": str(source.source_date or ""),
                "text": source.raw_text,
                # The spoken text is only part of what a video carries. Its
                # description and chapter titles routinely hold dimensions,
                # materials and prices that are never said out loud, and they
                # are already in the database - they were simply never sent.
                "description": source.description,
                "chapters": source.chapters,
                "tags": source.tags,
                "duration_seconds": source.duration_seconds,
                "spoken_language": source.transcript_language,
            }
        )
    for source in order.web_urls.filter(
        extract_status=ExtractStatus.EXTRACTED
    ).select_related("source_type"):
        sources.append(
            {
                "type": source.source_type.slug,
                "url": source.canonical_url or source.final_url or source.url,
                "label": source.title,
                "publisher": source.site_name or source.author,
                "published_at": str(source.source_date or ""),
                "text": source.raw_text,
                # jsonld is schema.org/Product as the page publishes it about
                # itself: brand, GTIN, price and properties, already
                # structured. Headings give back the shape of a spec table
                # that the plain text flattens into prose.
                "summary": source.meta_description,
                "headings": source.headings,
                "jsonld": source.jsonld,
                "opengraph": source.opengraph,
            }
        )
    print("EXTRACT._COLLECT_SOURCES - DONE", len(sources))
    return sources


def build_prompt(
    prompt_name: str,
    pipeline: str,
    product: dict,
    sources: list[dict],
    choices: dict[str, list[str]],
) -> tuple[str, str]:
    """Base prompt plus the fragment of this product group.

    Everything that is always collected lives in shared/base.md; the group file
    adds what only this group needs and may sharpen the base rules, because
    it is appended after them. Returns the prompt and the version string
    that gets stored with the result.
    """
    print("EXTRACT.BUILD_PROMPT - STARTED", prompt_name)
    base, base_version = read_prompt("shared/base.md")
    group, group_version = read_prompt(f"groups/{prompt_name}.md")

    fields = json.dumps(
        schema.json_schema(pipeline, choices), indent=2, ensure_ascii=False
    )
    rendered_product = json.dumps(product, indent=2, ensure_ascii=False)
    rendered_sources = json.dumps(sources, indent=2, ensure_ascii=False)
    prompt = (
        base.format(fields=fields, product=rendered_product, sources=rendered_sources)
        + "\n\n"
        + group
    )

    print("EXTRACT.BUILD_PROMPT - DONE", prompt_name)
    return prompt, f"{base_version}+{group_version}"


def analyse(
    prompt: str, pipeline: str, choices: dict[str, list[str]]
) -> tuple[str, str, str]:
    """Ask Claude for the German analysis.

    Returns (raw JSON answer, provider, model). The same German schema that
    went into the prompt is handed to Claude as the input of its tool; the
    English fields are added by translate.py before parse() validates both.
    """
    print("EXTRACT.ANALYSE - STARTED", pipeline)
    from .claude import ask

    answer, provider, model = ask(
        prompt,
        schema.json_schema(pipeline, choices),
        ANALYSIS_TOOL,
        "Save the finished German product analysis.",
    )
    print("EXTRACT.ANALYSE - DONE", model)
    return answer, provider, model
