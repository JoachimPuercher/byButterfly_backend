"""Ask Gemini, fall back to Claude when Gemini refuses.

Gemini runs on the free tier and answers with 403 or 429 once the quota is
spent. Claude takes over then. Anything else propagates: a rejected request
means the schema is wrong, and Claude would reject it for the same reason.

The answer is returned as raw JSON text and validated by schema.parse()
afterwards, so neither provider can smuggle a wrong shape into the database.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# Claude needs an explicit budget. A full product answer in both languages
# stays well under this; more than that is a runaway answer.
MAX_OUTPUT_TOKENS = 16000

# Free tier spent, or a key without access to the model.
GEMINI_OUT_OF_QUOTA = (403, 429)


def ask(prompt: str, json_schema: dict) -> str:
    from google.genai import errors

    try:
        return _gemini(prompt, json_schema)
    except errors.ClientError as error:
        if error.code not in GEMINI_OUT_OF_QUOTA:
            raise
        logger.warning("Gemini refused with %s, asking Claude.", error.code)
    return _claude(prompt, json_schema)


def _gemini(prompt: str, json_schema: dict) -> str:
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
    return _answer(interaction.output_text or "", settings.GEMINI_MODEL)


def _claude(prompt: str, json_schema: dict) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": json_schema}},
    )
    text = "".join(block.text for block in message.content if block.type == "text")
    return _answer(text, settings.ANTHROPIC_MODEL)


def _answer(text: str, model: str) -> str:
    if not text.strip():
        raise ValueError(f"{model} returned an empty answer.")
    logger.info("Answered by %s.", model)
    return text
