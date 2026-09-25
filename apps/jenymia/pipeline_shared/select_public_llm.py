"""Ask Gemini, fall back to Claude when Gemini refuses.

Gemini runs on the free tier and answers with 403 or 429 once the quota is
spent. Claude takes over then, and also when Gemini answers with nothing at
all - a safety filter or a truncated stream says nothing about whether the
question can be answered, and Claude usually answers it.

If both stay silent the run stops with a message naming both attempts, so
the admin shows why nothing was written instead of only the last failure.

Anything else propagates: a rejected request means the schema is wrong, and
Claude would reject it for the same reason.

The answer is returned as raw JSON text and validated by schema.parse()
afterwards, so neither provider can smuggle a wrong shape into the database.
Which provider and model produced it is returned alongside, because a
product has to stay traceable to what wrote it.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# Claude needs an explicit budget. A full product answer in both languages
# stays well under this; more than that is a runaway answer.
MAX_OUTPUT_TOKENS = 16000

# Free tier spent, or a key without access to the model.
GEMINI_OUT_OF_QUOTA = (403, 429)

GEMINI = "gemini"
CLAUDE = "claude"


class ProviderError(RuntimeError):
    """The second provider failed for a reason of its own. Carries what
    happened to the first one, because only the escaping exception reaches
    the order and the admin."""


class EmptyAnswerError(ValueError):
    """A provider returned nothing usable. Its own kind, so that an empty
    answer can hand over to the next provider while a genuine error still
    stops the run."""


def ask(prompt: str, json_schema: dict) -> tuple[str, str, str]:
    """Return (answer as JSON text, provider, model)."""
    from google.genai import errors

    try:
        return _gemini(prompt, json_schema)
    except errors.ClientError as error:
        if error.code not in GEMINI_OUT_OF_QUOTA:
            raise
        first = f"{settings.GEMINI_MODEL} refused with {error.code}"
    except EmptyAnswerError as error:
        first = str(error)

    # The two branches phrase it differently; one of them ends in a period.
    first = first.rstrip(".")
    logger.warning("%s - asking Claude.", first)
    try:
        return _claude(prompt, json_schema)
    except EmptyAnswerError as error:
        # Both silent. The order has to say that both were asked, otherwise
        # the admin shows only Claude and the operator never learns that
        # Gemini failed first - which is the part worth investigating.
        raise EmptyAnswerError(f"No provider answered. {first}. Then {error}") from None
    except Exception as error:
        # Not re-raising the provider's own class: its constructor takes
        # arguments we do not have. The class name goes into the message,
        # the original stays attached as the cause.
        raise ProviderError(f"{first}. Then {type(error).__name__}: {error}") from error


def _gemini(prompt: str, json_schema: dict) -> tuple[str, str, str]:
    from google import genai

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    interaction = client.interactions.create(
        model=settings.GEMINI_MODEL,
        input=prompt,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": json_schema,
        },
    )
    return _answer(interaction.output_text or "", GEMINI, settings.GEMINI_MODEL)


def _claude(prompt: str, json_schema: dict) -> tuple[str, str, str]:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": json_schema}},
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    return _answer(text, CLAUDE, settings.ANTHROPIC_MODEL)


def _answer(text: str, provider: str, model: str) -> tuple[str, str, str]:
    if not text.strip():
        raise EmptyAnswerError(f"{model} returned an empty answer.")
    logger.info("Answered by %s.", model)
    return text, provider, model
