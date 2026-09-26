"""Job 1: turn every source of an order into text.

Downloads nothing twice: a source that already carries text is skipped, so a
retry only repeats what actually failed.

Two kinds of failure are told apart. A network hiccup or a busy server is
transient: the job raises and rq runs it again later. A video that is too
long, a URL that is not YouTube, a 404 or a page without text is permanent:
retrying cannot change it, so the order fails right away instead of after
five rounds of re-fetching the other sources.

Web pages are fetched the same way everywhere (web.py). YouTube is the one
step bound to this machine, so it lives in its own package behind

    youtube_text(url: str) -> tuple[str, str, dict]

returning the transcript, the detected language and the metadata columns of
YoutubeUrl. A server needs a different implementation of that one function,
because YouTube blocks datacenter addresses and Railway has no GPU for
whisper (backlog 5.7). One import changes then, nothing else here.

Writing goes through services, never through Model.objects.
"""

import logging
from dataclasses import asdict

from django.conf import settings
from django.db import close_old_connections

from apps.jenymia import services
from apps.jenymia.models import ExtractStatus, ProductToAnalyse
from apps.jenymia.pipeline_local import youtube_text

from . import web
from .errors import EmptySourceError, PermanentSourceError

logger = logging.getLogger(__name__)


class IngestError(Exception):
    """At least one source failed for a transient reason. rq retries the
    job; the sources that already worked are skipped on the next run."""


def run_ingest(order_id: str) -> None:
    """Entry point for the queue.

    IngestError is passed on so rq retries the job. Everything else is a bug
    on our side: retrying cannot fix it, so the order records why it stopped
    instead of staying on `running`, which in the admin looks like a run that
    is still going.
    """
    print("INGEST.RUN_INGEST - STARTED", order_id)
    try:
        _ingest_all_sources(order_id)
    except IngestError:
        raise
    except Exception as error:
        logger.exception("Ingest failed for order %s", order_id)
        order = ProductToAnalyse.objects.filter(pk=order_id).first()
        if order is None:
            raise
        services.set_order_status(
            order,
            ProductToAnalyse.Status.FAILED,
            error=f"{type(error).__name__}: {error}",
        )
    print("INGEST.RUN_INGEST - DONE", order_id)


def _ingest_all_sources(order_id: str) -> None:
    print("INGEST._INGEST_ALL_SOURCES - STARTED", order_id)
    order = ProductToAnalyse.objects.get(pk=order_id)
    services.set_order_status(order, ProductToAnalyse.Status.RUNNING)
    attempt = order.attempts + 1

    transient: list[str] = []
    permanent: list[str] = []
    # Read as lists so the log can name the position of each source in the
    # whole run, not only that some source is being fetched.
    youtube_sources = list(order.youtube_urls.all())
    web_sources = list(order.web_urls.all())
    total = len(youtube_sources) + len(web_sources)
    position = 0
    for source in youtube_sources:
        position += 1
        print(f"INGEST._INGEST_ALL_SOURCES - SOURCE {position}/{total}", source.url)
        _ingest_one(source, _ingest_youtube, transient, permanent)
    for source in web_sources:
        position += 1
        print(f"INGEST._INGEST_ALL_SOURCES - SOURCE {position}/{total}", source.url)
        _ingest_one(source, _ingest_web, transient, permanent)

    if permanent or (transient and attempt >= settings.INGEST_MAX_ATTEMPTS):
        summary = " | ".join(permanent + transient)
        logger.error("Order %s failed on attempt %s: %s", order_id, attempt, summary)
        services.set_order_status(
            order,
            ProductToAnalyse.Status.FAILED,
            error=f"Failed on attempt {attempt}: {summary}",
        )
        print("INGEST._INGEST_ALL_SOURCES - DONE", order_id)
        return
    if transient:
        raise IngestError(" | ".join(transient))

    services.set_order_status(order, ProductToAnalyse.Status.TEXT_EXTRACTED)
    services.request_extract(order)
    print("INGEST._INGEST_ALL_SOURCES - DONE", order_id)


def _ingest_one(source, handler, transient: list[str], permanent: list[str]) -> None:
    print("INGEST._INGEST_ONE - STARTED", source.url)
    if source.extract_status == ExtractStatus.EXTRACTED and source.raw_text:
        logger.info("Skipping %s, already extracted.", source.url)
        print("INGEST._INGEST_ONE - DONE", source.url)
        return
    try:
        handler(source)
    except (PermanentSourceError, NotImplementedError) as error:
        logger.warning("Source rejected for good: %s (%s)", source.url, error)
        services.save_source_error(source, f"{type(error).__name__}: {error}")
        permanent.append(f"{source.url}: {error}")
    except Exception as error:
        logger.exception("Extraction failed for %s", source.url)
        services.save_source_error(source, f"{type(error).__name__}: {error}")
        transient.append(f"{source.url}: {error}")
    print("INGEST._INGEST_ONE - DONE", source.url)


def _ingest_youtube(source) -> None:
    print("INGEST._INGEST_YOUTUBE - STARTED", source.url)
    text, language, fields = youtube_text(source.url)
    # The transcription above can run for the better part of an hour without
    # a single query. A proxy in between (Railway's TCP proxy) drops a
    # connection idle that long, and Django only notices on the next query -
    # which would be the write below, losing the transcript. Closing first
    # makes that write open a fresh connection.
    close_old_connections()
    if not text.strip():
        raise EmptySourceError("Transcription returned no text.")
    services.save_source_text(
        source,
        text,
        transcript_language=language,
        # A date entered by hand wins over the one the video reports.
        source_date=source.source_date or fields.get("upload_date"),
        **fields,
    )
    print("INGEST._INGEST_YOUTUBE - DONE", source.url)


def _ingest_web(source) -> None:
    print("INGEST._INGEST_WEB - STARTED", source.url)
    result = web.fetch(source.url)
    if not result.raw_text.strip():
        raise EmptySourceError("No article text found on the page.")
    fields = asdict(result)
    raw_text = fields.pop("raw_text")
    services.save_source_text(
        source,
        raw_text,
        source_date=source.source_date
        or (result.published_at.date() if result.published_at else None),
        **fields,
    )
    print("INGEST._INGEST_WEB - DONE", source.url)
