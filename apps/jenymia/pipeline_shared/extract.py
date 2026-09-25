"""Job 2: turn the collected texts into one product.

Runs only after job 1 has produced text, and it can be repeated without
downloading anything again - the raw material stays in the database.

The provider sits behind analyse(). Swapping it touches that function and
nothing else: the prompt is already built, the expected answer is defined
once in schema.py, and every answer is validated before a row is written.
"""

import json
import logging
from pathlib import Path

from apps.jenymia import services
from apps.jenymia.models import ExtractStatus, ProductToAnalyse, SourceType

from . import schema

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def run_extract(order_id: str) -> None:
    order = ProductToAnalyse.objects.select_related("category").get(pk=order_id)

    # Everything that can fail is inside the try, including the pipeline
    # lookup and the prompt files. An exception thrown before it would leave
    # the order on text_extracted with an empty error, which in the admin is
    # indistinguishable from a run that is still going.
    try:
        pipeline = order.category.resolve_pipeline()

        sources = _collect_sources(order)
        if not sources:
            services.set_order_status(
                order,
                ProductToAnalyse.Status.FAILED,
                error="No source text to analyse.",
            )
            return

        prompt, prompt_version = build_prompt(pipeline, sources)
        answer, provider, model = analyse(prompt, pipeline)
        data = schema.parse(answer, pipeline)
        product = services.create_product_from_analysis(order, data)
    except Exception as error:
        logger.exception("Analysis failed for order %s", order_id)
        services.set_order_status(
            order,
            ProductToAnalyse.Status.FAILED,
            error=f"{type(error).__name__}: {error}",
        )
        return

    services.set_order_status(
        order,
        ProductToAnalyse.Status.ANALYSE_COMPLETE,
        prompt_version=prompt_version,
        llm_provider=provider,
        llm_model=model,
    )
    logger.info("Created product %s from order %s.", product.pk, order_id)


def _collect_sources(order: ProductToAnalyse) -> list[dict]:
    """Every source that produced text, with the metadata the model needs to
    weigh it: who said it, what kind of publication it is, and when."""
    sources = []
    for source in order.youtube_urls.filter(extract_status=ExtractStatus.EXTRACTED):
        sources.append(
            {
                "type": SourceType.YOUTUBE,
                "url": source.webpage_url or source.url,
                "label": source.title,
                "publisher": source.channel,
                "published_at": str(source.source_date or ""),
                "text": source.raw_text,
            }
        )
    for source in order.web_urls.filter(extract_status=ExtractStatus.EXTRACTED):
        sources.append(
            {
                "type": source.source_type,
                "url": source.canonical_url or source.final_url or source.url,
                "label": source.title,
                "publisher": source.site_name or source.author,
                "published_at": str(source.source_date or ""),
                "text": source.raw_text,
            }
        )
    return sources


def build_prompt(pipeline: str, sources: list[dict]) -> tuple[str, str]:
    """Base prompt plus the fragment of this pipeline.

    Everything that is always collected lives in _base.md; the group file
    adds what only this group needs and may sharpen the base rules, because
    it is appended after them. Returns the prompt and the version string
    that gets stored with the result.
    """
    base, base_version = _read_prompt("_base.md")
    group, group_version = _read_prompt(f"{pipeline}.md")

    fields = json.dumps(schema.json_schema(pipeline), indent=2, ensure_ascii=False)
    rendered_sources = json.dumps(sources, indent=2, ensure_ascii=False)
    prompt = base.format(fields=fields, sources=rendered_sources) + "\n\n" + group

    return prompt, f"{base_version}+{group_version}"


def _read_prompt(name: str) -> tuple[str, str]:
    """The first line of every prompt file is 'version: <id>'. The file is
    the history (git), the version string is what makes it possible to find
    out later which products a given prompt produced.

    _base.md goes through str.format, so it may contain no braces except the
    two placeholders {fields} and {sources}. The group files are appended
    unformatted and may use braces freely."""
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    first_line, _, body = text.partition("\n")
    if not first_line.startswith("version:"):
        raise ValueError(f"Prompt {name} has no version line.")
    return body.strip(), first_line.split(":", 1)[1].strip()


def analyse(prompt: str, pipeline: str) -> tuple[str, str, str]:
    """Ask the language model for the analysis.

    Returns (raw JSON answer, provider, model); parse() validates the answer
    before a row is written. The same schema that went into the prompt is
    handed to the structured output mode, so the shape is enforced while the
    answer is generated and checked again afterwards. Which provider answers
    is decided in select_public_llm.
    """
    from .select_public_llm import ask

    return ask(prompt, schema.json_schema(pipeline))
