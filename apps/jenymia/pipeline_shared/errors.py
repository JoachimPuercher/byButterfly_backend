"""Errors that mean "do not try again".

The shared ingest job tells transient failures (network, busy server) apart
from permanent ones (video too long, not a YouTube URL, page without text).
Every backend raises a subclass of PermanentSourceError for the latter, so
the job never has to know which backend is running.
"""


class PermanentSourceError(Exception):
    """The source cannot be used. Retrying will not change that."""


class EmptySourceError(PermanentSourceError):
    """The source yielded no usable text."""


class RejectedUrlError(PermanentSourceError):
    """The URL is not one we fetch."""
