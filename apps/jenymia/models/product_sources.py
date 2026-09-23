from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class ProductToAnalyse(BaseModel):
    class Status(models.TextChoices):
        NOT_ANALYSED = "not_analysed", "not analysed"
        ALREADY_ANALYSED = "already_analysed", "already analysed"

    class AnalysedBy(models.TextChoices):
        ADMIN = "admin", "admin"
        USER = "user", "user"

    title = models.CharField(max_length=200)
    brand = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NOT_ANALYSED,
    )
    last_analysed_at = models.DateField(null=True, blank=True)
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


class YoutubeUrl(BaseModel):
    product = models.ForeignKey(
        ProductToAnalyse,
        on_delete=models.CASCADE,
        related_name="youtube_urls",
    )
    url = models.URLField(max_length=500)
    raw_text = models.TextField(blank=True)
    source_date = models.DateField(null=True, blank=True)


class WebUrl(BaseModel):
    product = models.ForeignKey(
        ProductToAnalyse,
        on_delete=models.CASCADE,
        related_name="web_urls",
    )
    url = models.URLField(max_length=500)
    raw_text = models.TextField(blank=True)
    source_date = models.DateField(null=True, blank=True)
