"""YouTube: metadata and audio.

Metadata and audio come from the same yt-dlp call, so nothing is downloaded
for a video that is rejected for being too long. The dataclass field names
are the YoutubeUrl columns.

Only YouTube hosts are accepted and only the YouTube extractor is enabled:
yt-dlp would otherwise fall back to its generic extractor and fetch any
http(s) address, including ones inside the private network.

Runs locally today. On a server this is the one step that is not portable:
YouTube blocks datacenter IP ranges, so the same call that works from a
laptop fails from Railway. Backlog 5.7 replaces this with an external API
behind the same youtube_text(); the shared pipeline does not change.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yt_dlp
from django.conf import settings

from apps.jenymia.pipeline_shared.errors import PermanentSourceError, RejectedUrlError

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")
# Audio only, so this is generous: an hour of best-quality audio is ~60 MB.
MAX_AUDIO_BYTES = 200 * 1024 * 1024


class VideoTooLongError(PermanentSourceError):
    """Raised before the download starts, not after."""


@dataclass
class YoutubeSource:
    audio_path: str
    video_id: str
    webpage_url: str
    title: str
    channel: str
    channel_id: str
    channel_url: str
    upload_date: date | None
    duration_seconds: int | None
    license: str
    availability: str
    description: str
    tags: list[str] = field(default_factory=list)
    chapters: list[dict] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


# Kept in meta next to the mapped columns. Not the whole yt-dlp response:
# that carries every stream format and thumbnail and would put megabytes of
# noise into the row.
_EXTRA_KEYS = (
    "original_url",
    "categories",
    "language",
    "age_limit",
    "live_status",
    "view_count",
    "like_count",
    "comment_count",
    "thumbnail",
)


def fetch(url: str) -> YoutubeSource:
    """Read the metadata, then download the audio track."""
    if urlparse(url).hostname not in ALLOWED_HOSTS:
        raise RejectedUrlError(f"Not a YouTube URL: {url}")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(Path(settings.JENYMIA_AUDIO_DIR) / "%(id)s.%(ext)s"),
        "allowed_extractors": ["youtube"],
        "max_filesize": MAX_AUDIO_BYTES,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    Path(settings.JENYMIA_AUDIO_DIR).mkdir(parents=True, exist_ok=True)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        duration = info.get("duration")
        if duration is None or duration > settings.MAX_VIDEO_DURATION_SECONDS:
            minutes = settings.MAX_VIDEO_DURATION_SECONDS // 60
            raise VideoTooLongError(
                f"Videos longer than {minutes} minutes are not supported."
            )

        info = ydl.extract_info(url, download=True)
        # The path yt-dlp actually wrote; the template name can differ when
        # it picks another container than expected.
        audio_path = info["requested_downloads"][0]["filepath"]

    return YoutubeSource(
        audio_path=audio_path,
        video_id=info.get("id", ""),
        webpage_url=info.get("webpage_url", ""),
        title=(info.get("title") or "")[:300],
        # channel and uploader are the same name in most cases; uploader is
        # the fallback for older videos where channel is missing.
        channel=(info.get("channel") or info.get("uploader") or "")[:200],
        channel_id=info.get("channel_id") or "",
        channel_url=info.get("channel_url") or info.get("uploader_url") or "",
        upload_date=_parse_upload_date(info.get("upload_date")),
        duration_seconds=info.get("duration"),
        license=(info.get("license") or "")[:120],
        availability=(info.get("availability") or "")[:60],
        description=info.get("description") or "",
        tags=info.get("tags") or [],
        chapters=info.get("chapters") or [],
        meta={key: info.get(key) for key in _EXTRA_KEYS},
    )


def _parse_upload_date(value: str | None) -> date | None:
    """yt-dlp reports the date as YYYYMMDD."""
    if not value:
        return None
    return datetime.strptime(value, "%Y%m%d").date()
