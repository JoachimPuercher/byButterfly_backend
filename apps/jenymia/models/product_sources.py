"""The analysis order and its raw material.

ProductToAnalyse is what gets entered in the admin: a product name, a brand,
a main category and a handful of URLs. The pipeline turns it into a Product.

YoutubeUrl and WebUrl hold the raw material - transcripts, page text and
metadata. That text never leaves the backend: it is a reproduction of
someone else's work and is only an input for our own analysis. Nothing here
is serialized into the public API. The public source list (ProductSource)
is derived from these rows, so title, channel, site name and dates are what
readers later see as the citation.
"""

from django.conf import settings
from django.db import models

from apps.common.models import BaseModel

from .base import SourceType
from .lookups import MainCategory, SubCategory


class ProductToAnalyse(BaseModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "queued"
        RUNNING = "running", "running"
        TEXT_EXTRACTED = "text_extracted", "text extracted"
        ANALYSE_COMPLETE = "analyse_complete", "analysis complete"
        FAILED = "failed", "failed"

    class AnalysedBy(models.TextChoices):
        ADMIN = "admin", "admin"
        USER = "user", "user"

    title = models.CharField(max_length=200)
    brand = models.CharField(max_length=100)
    # Picks the prompt, and becomes the product's home. Chosen by hand,
    # never guessed by the analysis.
    primary_category = models.CharField(max_length=20, choices=MainCategory.choices)
    # Optional. Only relevant when a group of products inside a main category
    # needs its own wording: if this sub-category carries a prompt_name, that
    # file is used instead of the main category's.
    sub_category = models.ForeignKey(
        SubCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="analysis_orders",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    # Filled when the run fails for good; shown in the admin. There is no
    # mail delivery yet, this column is the error report.
    error = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    # Which prompt version produced this analysis, e.g. "toys-2026-09-24".
    # Without it there is no way to find out later which products were
    # written by a prompt that turned out to be wrong.
    prompt_version = models.CharField(max_length=60, blank=True)
    # Which model answered. Together with prompt_version this makes a product
    # traceable to what produced it, which is what a re-run needs to target.
    llm_provider = models.CharField(max_length=20, blank=True)
    llm_model = models.CharField(max_length=60, blank=True)
    last_analysed_at = models.DateTimeField(null=True, blank=True)
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="products_to_analyse",
    )
    analysed_by = models.CharField(
        max_length=10,
        choices=AnalysedBy.choices,
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["status"])]

    def __str__(self) -> str:
        return f"{self.brand} {self.title}"


class ExtractStatus(models.TextChoices):
    PENDING = "pending", "pending"
    EXTRACTED = "extracted", "extracted"
    FAILED = "failed", "failed"


class SourceUrlBase(BaseModel):
    """Shared columns of every raw source: where it came from, what text was
    pulled out of it, whether that worked, and the parts of its metadata
    that end up in the public citation."""

    url = models.URLField(max_length=500)
    raw_text = models.TextField(blank=True)
    extract_status = models.CharField(
        max_length=10, choices=ExtractStatus.choices, default=ExtractStatus.PENDING
    )
    error = models.TextField(blank=True)
    # Publication date of the source. Entered by hand or filled by the
    # pipeline when it is still empty; a hand-entered value wins.
    source_date = models.DateField(null=True, blank=True)
    title = models.CharField(max_length=300, blank=True)
    # Anything the fetcher reported that has no column of its own.
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["product", "url"],
                name="%(app_label)s_%(class)s_url_unique_per_order",
            )
        ]

    def __str__(self) -> str:
        return self.title or self.url


class YoutubeUrl(SourceUrlBase):
    product = models.ForeignKey(
        ProductToAnalyse,
        on_delete=models.CASCADE,
        related_name="youtube_urls",
    )
    video_id = models.CharField(max_length=20, blank=True)
    # Canonical URL after yt-dlp resolved shorts, playlists and redirects.
    webpage_url = models.URLField(max_length=500, blank=True)
    channel = models.CharField(max_length=200, blank=True)
    # Survives channel renames, unlike the name.
    channel_id = models.CharField(max_length=60, blank=True)
    channel_url = models.URLField(max_length=500, blank=True)
    upload_date = models.DateField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    license = models.CharField(max_length=120, blank=True)
    availability = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)
    tags = models.JSONField(default=list, blank=True)
    chapters = models.JSONField(default=list, blank=True)
    # Whisper reports which language it heard; useful when a source turns out
    # to be in a language we did not expect.
    transcript_language = models.CharField(max_length=10, blank=True)

    class Meta(SourceUrlBase.Meta):
        pass


class WebUrl(SourceUrlBase):
    """A web page source. Three views are taken of the same HTML: the clean
    article text, the head metadata, and the structured data (JSON-LD,
    OpenGraph) that the page publishes about itself."""

    product = models.ForeignKey(
        ProductToAnalyse,
        on_delete=models.CASCADE,
        related_name="web_urls",
    )
    # What kind of page this is - the operator knows, the fetcher cannot.
    # Ends up as the type of the public citation.
    source_type = models.CharField(
        max_length=20,
        choices=[c for c in SourceType.choices if c[0] != SourceType.YOUTUBE],
    )
    # Fetch result, kept for error analysis and re-crawls.
    final_url = models.URLField(max_length=1000, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True)

    meta_description = models.TextField(blank=True)
    canonical_url = models.URLField(max_length=1000, blank=True)
    site_name = models.CharField(max_length=200, blank=True)
    author = models.CharField(max_length=200, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    modified_at = models.DateTimeField(null=True, blank=True)
    language = models.CharField(max_length=10, blank=True)

    headings = models.JSONField(default=list, blank=True)
    jsonld = models.JSONField(default=list, blank=True)
    opengraph = models.JSONField(default=dict, blank=True)

    class Meta(SourceUrlBase.Meta):
        pass
