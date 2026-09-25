"""Speech to text with faster-whisper on this machine.

The language is never forced: whisper detects what is actually spoken and
reports it back, so a source in an unexpected language shows up as such
instead of being mistranslated into German.

This is the part that does not move to a server: Railway has no GPUs and a
large model on a CPU runs at roughly real time. A server build calls a
hosted service instead and returns the same tuple (backlog 5.7).
"""

import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_model():
    """Loading the model is the slow part, so it happens once per worker
    process, not once per job."""
    print("TRANSCRIBE._GET_MODEL - STARTED", settings.WHISPER_MODEL)
    from faster_whisper import WhisperModel

    model = WhisperModel(
        settings.WHISPER_MODEL,
        device=settings.WHISPER_DEVICE,
        compute_type=settings.WHISPER_COMPUTE_TYPE,
    )
    print("TRANSCRIBE._GET_MODEL - DONE", settings.WHISPER_MODEL)
    return model


def transcribe(audio_path: str) -> tuple[str, str]:
    """Return (text, detected language code)."""
    print("TRANSCRIBE.TRANSCRIBE - STARTED", audio_path)
    segments, info = _get_model().transcribe(audio_path)
    # segments is a generator: the work happens while it is consumed.
    text = " ".join(segment.text.strip() for segment in segments)
    logger.info("Transcribed %s (%s).", audio_path, info.language)
    print("TRANSCRIBE.TRANSCRIBE - DONE", audio_path)
    return text, info.language
