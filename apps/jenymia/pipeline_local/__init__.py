"""The YouTube step, bound to this machine: yt-dlp downloads the audio,
faster-whisper transcribes it.

Separate from pipeline_shared because it is the only part that cannot move
to a server as it is. YouTube blocks datacenter addresses and Railway has no
GPU, so a server needs another implementation of youtube_text() (backlog
5.7). Everything about files, models and YouTube access stays in here.
"""

from dataclasses import asdict
from pathlib import Path

from . import transcribe, youtube


def youtube_text(url: str) -> tuple[str, str, dict]:
    """Transcript, detected language and the YoutubeUrl metadata columns.

    The audio file is deleted whether or not the transcription worked - it
    is only an intermediate step and would otherwise pile up."""
    print("PIPELINE_LOCAL.YOUTUBE_TEXT - STARTED", url)
    result = youtube.fetch(url)
    try:
        text, language = transcribe.transcribe(result.audio_path)
    finally:
        Path(result.audio_path).unlink(missing_ok=True)
    fields = asdict(result)
    fields.pop("audio_path")
    print("PIPELINE_LOCAL.YOUTUBE_TEXT - DONE", url)
    return text, language, fields
