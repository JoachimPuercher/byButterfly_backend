"""Ask Claude for a structured answer.

Used twice per analysis: once for the German analysis, once for its English
translation (translate.py). Each call hands Claude one tool whose input
schema is the shape of the answer, not the structured output mode: that
mode compiles the schema into a grammar, and the analysis schema is too
large for it. A tool without strict mode takes the schema as it is,
including its value constraints, and the answer arrives as a ready JSON
object.

The shape is therefore not enforced while the answer is generated. It is
enforced afterwards by schema.parse(), before anything is written.
"""

import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

PROVIDER = "claude"

# Both languages, up to eight FAQ entries and twelve pros and cons add up.
# A budget of 16000 truncated long answers, and a truncated answer arrives as
# a validation error that says nothing about the real cause.
MAX_OUTPUT_TOKENS = 32000


class EmptyAnswerError(ValueError):
    """The model returned nothing usable, or stopped before it was finished.
    Its own kind, so an unusable answer stays distinguishable in the order's
    error from a request the provider refused outright."""


def ask(
    prompt: str, json_schema: dict, tool_name: str, tool_description: str
) -> tuple[str, str, str]:
    """Return (answer as JSON text, provider, model).

    The answer is the input Claude passes to the tool `tool_name`.
    """
    print("CLAUDE.ASK - STARTED", tool_name, settings.ANTHROPIC_MODEL)
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    # Streamed because the SDK refuses a plain request whose output budget
    # could take longer than ten minutes to generate. get_final_message()
    # still hands back one complete message, as create() would.
    with client.messages.stream(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        # temperature stays unset. The API default is what produces prose
        # that reads like a person wrote it; lowering it is what makes every
        # analysis sound like the last one.
        system=(
            f"Deliver your answer by calling the tool {tool_name}. "
            "Never answer with plain text."
        ),
        messages=[{"role": "user", "content": prompt}],
        tools=[
            {
                "name": tool_name,
                "description": tool_description,
                "input_schema": json_schema,
            }
        ],
        # Forcing the tool (tool_choice "tool" or "any") is not supported by
        # the current models, so the system prompt asks for it instead and
        # the missing call is treated as an unusable answer below.
        tool_choice={"type": "auto", "disable_parallel_tool_use": True},
    ) as stream:
        message = stream.get_final_message()

    # A cut-off answer is incomplete JSON, so it would surface in parse() as
    # a complaint about a missing field. Saying it here names the real cause.
    if message.stop_reason == "max_tokens":
        raise EmptyAnswerError(
            f"{settings.ANTHROPIC_MODEL} hit the output limit of "
            f"{MAX_OUTPUT_TOKENS} tokens; the answer is incomplete."
        )
    call = next((block for block in message.content if block.type == "tool_use"), None)
    if call is None or not call.input:
        raise EmptyAnswerError(
            f"{settings.ANTHROPIC_MODEL} did not call {tool_name} "
            f"(stop_reason {message.stop_reason!r})."
        )
    logger.info("%s answered by %s.", tool_name, settings.ANTHROPIC_MODEL)
    print("CLAUDE.ASK - DONE", tool_name, settings.ANTHROPIC_MODEL)
    return (
        json.dumps(call.input, ensure_ascii=False),
        PROVIDER,
        settings.ANTHROPIC_MODEL,
    )
