"""Step 2 of the analysis: the English translation of the German answer.

The analysis is written in German only (extract.py). Every German text of
the answer then goes to Claude once more, keyed by its path in the answer -
"translations.hook", "faqs.2.answer" - and comes back in English under the
same path. Keyed by path, nothing can shift: an entry the model leaves out
or reorders is simply missing, and asked for once more.

English is therefore always the translation of the German statements,
never a second text written next to them.
"""

import copy
import json
import logging
from typing import Any

from . import schema
from .prompt_files import read_prompt

logger = logging.getLogger(__name__)

TRANSLATION_TOOL = "save_translation"
# The first request, and one more for whatever it left out.
MAX_ROUNDS = 2


def translate(
    answer: dict[str, Any], pipeline: str, order_facts: dict[str, str]
) -> tuple[dict[str, Any], str]:
    """Return the answer with its English fields filled, and the version of
    the translation prompt.

    A text still untranslated after the second round stays without English
    and gets the English placeholder in parse() - logged, and refused on
    publication until it is filled in by hand.
    """
    print("TRANSLATE.TRANSLATE - STARTED", pipeline)
    template, version = read_prompt("shared/translate.md")
    answer = with_german_title(answer, order_facts)
    german = german_texts(answer, pipeline)
    english: dict[str, str] = {}
    missing = german
    for _ in range(MAX_ROUNDS):
        if not missing:
            break
        english.update(ask_translation(template, missing, pipeline))
        missing = {
            path: text
            for path, text in german.items()
            if not english.get(path, "").strip()
        }
    if missing:
        logger.warning(
            "No English for %s after %s rounds.", sorted(missing), MAX_ROUNDS
        )
    print("TRANSLATE.TRANSLATE - DONE", len(english), "of", len(german))
    return with_english(answer, english), version


def with_german_title(
    answer: dict[str, Any], order_facts: dict[str, str]
) -> dict[str, Any]:
    """The answer with a German title. Without one there is nothing to
    translate into the English title; the title entered on the order is
    German and stands in, logged."""
    answer = copy.deepcopy(answer)
    if not isinstance(answer.get("translations"), dict):
        answer["translations"] = {}
    if not isinstance(answer["translations"].get("de"), dict):
        answer["translations"]["de"] = {}
    german = answer["translations"]["de"]
    title = german.get("title")
    if not (isinstance(title, str) and title.strip()):
        german["title"] = order_facts.get("title", "")
        logger.warning("No de title in the answer; using %r.", german["title"])
    return answer


def german_texts(answer: dict[str, Any], pipeline: str) -> dict[str, str]:
    """Every non-empty German text of the answer, keyed by its path.

    Only the fields and lists of this pipeline's answer: anything else is
    dropped by parse() anyway and would only cost translation tokens."""
    texts: dict[str, str] = {}
    german = (answer.get("translations") or {}).get("de")
    if isinstance(german, dict):
        for field in schema.text_fields(pipeline):
            _add_text(texts, f"translations.{field}", german.get(field))
    fields = schema.ANALYSIS_MODELS[pipeline].model_fields
    for list_name, entry_class in schema.TRANSLATED_LISTS.items():
        entries = answer.get(list_name)
        if list_name not in fields or not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            for field in entry_class.TRANSLATED:
                _add_text(
                    texts, f"{list_name}.{index}.{field}", entry.get(f"{field}_de")
                )
    return texts


def _add_text(texts: dict[str, str], path: str, value: Any) -> None:
    # A number where text was asked for (a spec value of 38) is text too.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if isinstance(value, str) and value.strip():
        texts[path] = value


def with_english(answer: dict[str, Any], english: dict[str, str]) -> dict[str, Any]:
    """The answer with every translated text set as its English field: the
    `en` block of the long texts, `<field>_en` in the list entries."""
    merged = copy.deepcopy(answer)
    if not isinstance(merged.get("translations"), dict):
        merged["translations"] = {}
    english_block: dict[str, str] = {}
    for path, text in english.items():
        parts = path.split(".")
        if parts[0] == "translations":
            english_block[parts[1]] = text
        else:
            list_name, index, field = parts
            merged[list_name][int(index)][f"{field}_en"] = text
    merged["translations"]["en"] = english_block
    return merged


def ask_translation(
    template: str, texts: dict[str, str], pipeline: str
) -> dict[str, str]:
    """One request: the German texts in, the English texts under the same
    paths out. Paths the answer does not know are ignored."""
    from .claude import ask

    prompt = template.format(texts=json.dumps(texts, indent=2, ensure_ascii=False))
    tool_schema = {
        "type": "object",
        "properties": {path: _english_property(pipeline, path) for path in texts},
        "required": list(texts),
        "additionalProperties": False,
    }
    answer_text, _, _ = ask(
        prompt,
        tool_schema,
        TRANSLATION_TOOL,
        "Save the English text for every path of the German analysis.",
    )
    answer = json.loads(answer_text)
    return {
        path: value
        for path, value in answer.items()
        if path in texts and isinstance(value, str)
    }


def _english_property(pipeline: str, path: str) -> dict[str, Any]:
    """The tool property for one path, with the column limit where there is
    one - an English text is often longer than its German original."""
    parts = path.split(".")
    if parts[0] == "translations":
        field = schema.text_model(pipeline).model_fields[parts[1]]
    else:
        field = schema.TRANSLATED_LISTS[parts[0]].model_fields[f"{parts[2]}_en"]
    limit = next(
        (m.max_length for m in field.metadata if getattr(m, "max_length", None)),
        None,
    )
    if limit is None:
        return {"type": "string"}
    return {"type": "string", "description": f"At most {limit} characters."}
