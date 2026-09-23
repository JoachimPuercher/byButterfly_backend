import uuid

from django.db import models


class BaseModel(models.Model):
    """Abstract base for every model: UUID primary key and timestamps.

    Abstract means no table of its own; the fields are added to each
    inheriting model.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
