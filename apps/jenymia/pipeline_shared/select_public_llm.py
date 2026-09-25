"""Ask the language model named in USE_LLM_MODEL for the analysis.

One model per run, chosen by configuration - not a chain that falls back.
Which model wrote a text decides how that text reads, so a page whose model
depended on whose quota happened to be spent was a page whose quality could
not be reproduced. The setting names one, and the order records which one
answered.

Claude writes the published analyses. Gemini Flash is the cheap seat for test
runs: switching USE_LLM_MODEL to gemini costs nothing per prompt, which makes
it the one to iterate a prompt against before spending Claude tokens on it.

The answer is returned as raw JSON text and validated by schema.parse()
afterwards, so neither model can smuggle a wrong shape into the database.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# Both languages, up to eight FAQ entries and twelve pros and cons add up.
# The previous budget of 16000 truncated long answers, and a truncated answer
# arrives as a validation error that says nothing about the real cause. Both
# models get the same budget, so switching between them does not silently
# change how much either is allowed to write.
MAX_OUTPUT_TOKENS = 32000

CLAUDE = "claude"
GEMINI = "gemini"


class EmptyAnswerError(ValueError):
    """The model returned nothing usable, or stopped before it was finished.
    Its own kind, so an unusable answer stays distinguishable in the order's
    error from a request the provider refused outright."""


def ask(prompt: str, json_schema: dict) -> tuple[str, str, str]:
    """Return (answer as JSON text, provider, model).

    settings validates USE_LLM_MODEL at startup, so the lookup below cannot
    miss for a running process - a typo stops the process instead of the job.
    """
    print("SELECT_PUBLIC_LLM.ASK - STARTED", settings.USE_LLM_MODEL)
    answer = {CLAUDE: _claude, GEMINI: _gemini}[settings.USE_LLM_MODEL](
        prompt, json_schema
    )
    print("SELECT_PUBLIC_LLM.ASK - DONE", settings.USE_LLM_MODEL)
    return answer


def _claude(prompt: str, json_schema: dict) -> tuple[str, str, str]:
    print("SELECT_PUBLIC_LLM._CLAUDE - STARTED", settings.ANTHROPIC_MODEL)
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        # temperature stays unset for both models. The API default is what
        # produces prose that reads like a person wrote it; lowering it is
        # what makes every analysis sound like the last one.
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": json_schema}},
    )
    text = "".join(block.text for block in message.content if block.type == "text")

    # A cut-off answer is not valid JSON, so it would surface in parse() as a
    # complaint about a missing brace. Saying it here names the actual cause.
    if message.stop_reason == "max_tokens":
        raise EmptyAnswerError(
            f"{settings.ANTHROPIC_MODEL} hit the output limit of "
            f"{MAX_OUTPUT_TOKENS} tokens; the answer is incomplete."
        )
    print("SELECT_PUBLIC_LLM._CLAUDE - DONE", settings.ANTHROPIC_MODEL)
    return _answer(text, CLAUDE, settings.ANTHROPIC_MODEL)


def _gemini(prompt: str, json_schema: dict) -> tuple[str, str, str]:
    print("SELECT_PUBLIC_LLM._GEMINI - STARTED", settings.GEMINI_MODEL)
    from google import genai

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    interaction = client.interactions.create(
        model=settings.GEMINI_MODEL,
        input=prompt,
        generation_config={"max_output_tokens": MAX_OUTPUT_TOKENS},
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": json_schema,
        },
    )

    # "incomplete" is what Gemini reports where Claude says max_tokens, and
    # any other non-completed status means the text in hand is partial too.
    if interaction.status != "completed":
        raise EmptyAnswerError(
            f"{settings.GEMINI_MODEL} stopped with status "
            f"{interaction.status!r}; the answer is incomplete."
        )
    print("SELECT_PUBLIC_LLM._GEMINI - DONE", settings.GEMINI_MODEL)
    return _answer(interaction.output_text or "", GEMINI, settings.GEMINI_MODEL)


def _answer(text: str, provider: str, model: str) -> tuple[str, str, str]:
    print("SELECT_PUBLIC_LLM._ANSWER - STARTED", model)
    if not text.strip():
        raise EmptyAnswerError(f"{model} returned an empty answer.")
    logger.info("Answered by %s.", model)
    print("SELECT_PUBLIC_LLM._ANSWER - DONE", model)
    return text, provider, model
