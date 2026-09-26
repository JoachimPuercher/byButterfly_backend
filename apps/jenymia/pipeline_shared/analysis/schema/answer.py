"""The answer of each main category, and what is done with it.

ANALYSIS_MODELS picks the answer shape by the slug of the main category -
the shared part (shared.py) plus the group's own (toys.py, school.py,
tech.py). json_schema() turns a shape into what Claude is shown and handed
as its tool, parse() validates what comes back.
"""

from typing import Any

from .school import SchoolAnalysis
from .shared import (
    CHOICES_MARKER,
    BadgeIn,
    BaseAnalysis,
    BaseText,
    FaqIn,
    FlatTranslations,
    ProsConIn,
    SpecIn,
    SubCategoryIn,
)
from .tech import DataCategoryIn, TechAnalysis
from .toys import LearningBadgeIn, ToysAnalysis

# Keyed by the slug of the main category (MainCategory.slug), which is also
# the name of its prompt file. Indexing with an unknown pipeline raises,
# which is wanted: a typo must not produce a base-only schema that validates.
ANALYSIS_MODELS: dict[str, type[BaseAnalysis]] = {
    "toys_learning": ToysAnalysis,
    "school_everyday": SchoolAnalysis,
    "tech_safety": TechAnalysis,
}

# The list fields of an answer whose entries carry translated fields, and the
# entry class that names them (TRANSLATED). translate.py walks these.
TRANSLATED_LISTS: dict[str, type[FlatTranslations]] = {
    "sub_categories": SubCategoryIn,
    "badges": BadgeIn,
    "learning_badges": LearningBadgeIn,
    "specs": SpecIn,
    "faqs": FaqIn,
    "pros_cons": ProsConIn,
    "data_categories": DataCategoryIn,
}


def text_model(pipeline: str) -> type[BaseText]:
    """The per-language text block of a pipeline's answer (SchoolText, ...)."""
    translated = ANALYSIS_MODELS[pipeline].model_fields["translations"].annotation
    return translated.model_fields["de"].annotation


def text_fields(pipeline: str) -> tuple[str, ...]:
    """The fields of the per-language text block of a pipeline's answer."""
    return tuple(text_model(pipeline).model_fields)


def _require_every_property(node: Any) -> None:
    """Mark every property of every object in the schema as required."""
    if isinstance(node, dict):
        if isinstance(node.get("properties"), dict):
            node["required"] = list(node["properties"])
        for value in node.values():
            _require_every_property(value)
    elif isinstance(node, list):
        for value in node:
            _require_every_property(value)


def _insert_choices(node: Any, choices: dict[str, list[str]]) -> None:
    """Turn every choices marker into an enum of that list's slugs.

    An empty list stops here: an empty enum is a schema no answer can
    satisfy, and the analysis would fail later without saying why.
    """
    if isinstance(node, dict):
        list_name = node.pop(CHOICES_MARKER, None)
        if list_name is not None:
            if not choices.get(list_name):
                raise ValueError(
                    f"The choice list {list_name!r} is empty; add its values in "
                    "the admin before analysing."
                )
            node["enum"] = choices[list_name]
        for value in node.values():
            _insert_choices(value, choices)
    elif isinstance(node, list):
        for value in node:
            _insert_choices(value, choices)


def _drop_english(result: dict[str, Any]) -> None:
    """Remove every English field: the `en` block of the long texts and the
    `<field>_en` of every list entry. What is left is the German analysis."""
    entry_classes = {cls.__name__: cls for cls in FlatTranslations.__subclasses__()}
    for name, definition in result.get("$defs", {}).items():
        properties = definition.get("properties", {})
        if name in entry_classes:
            english = {f"{field}_en" for field in entry_classes[name].TRANSLATED}
        elif name.startswith("Translated_"):
            english = {"en"}
        else:
            continue
        for field in english:
            properties.pop(field, None)
        definition["required"] = [
            field for field in definition.get("required", []) if field not in english
        ]


def json_schema(pipeline: str, choices: dict[str, list[str]]) -> dict[str, Any]:
    """The shape of the German analysis, for the prompt and for Claude's tool.

    The analysis is written in German only; translate.py adds the English
    fields afterwards, and parse() validates both together. So this is the
    full answer shape without its English fields.

    `choices` holds the slugs of every choice list the answer refers to
    (selectors.analysis_choices), so the enums the model sees are the rows
    in the database - not a copy of them in code.

    Every property is marked required here, although nearly every field has
    a default. A field without a default is required in both directions: the
    model has to write it, and pydantic rejects the whole answer when it does
    not. Only the first half is wanted. Marking them required in the emitted
    schema makes the model write every field, while the defaults keep a thin
    answer alive.
    """
    print("SCHEMA.JSON_SCHEMA - STARTED", pipeline)
    result = ANALYSIS_MODELS[pipeline].model_json_schema()
    _insert_choices(result, choices)
    _require_every_property(result)
    _drop_english(result)
    print("SCHEMA.JSON_SCHEMA - DONE", pipeline)
    return result


def parse(
    payload: str | dict[str, Any],
    pipeline: str,
    order_facts: dict[str, str],
    choices: dict[str, list[str]],
) -> dict[str, Any]:
    """Validate the answer and hand back plain data.

    order_facts carries what the order already knows - title and brand - and
    is what the answer falls back to where it has nothing usable for them.
    choices are the slugs the model was offered (the same dict json_schema
    got); a choice field outside them is dropped here, before the
    placeholders are decided.

    Missing or unusable values fall back field by field (see the classes
    above), so this raises pydantic.ValidationError only for an answer that
    is not an object at all.

    Returns a dict and not the model: services is the write interface for the
    whole app and must not depend on pipeline types.
    """
    print("SCHEMA.PARSE - STARTED", pipeline)
    model = ANALYSIS_MODELS[pipeline]
    context = {**order_facts, "choices": choices}
    result = (
        model.model_validate_json(payload, context=context)
        if isinstance(payload, str)
        else model.model_validate(payload, context=context)
    )
    print("SCHEMA.PARSE - DONE", pipeline)
    return result.model_dump()
