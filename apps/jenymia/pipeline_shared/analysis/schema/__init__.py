"""The shape of the analysis answer.

shared.py      what every product group returns, and the building blocks
toys.py        what only Spielen & Lernen returns
school.py      what only Schule & Alltag returns
tech.py        what only Tech & Sicherheit returns
answer.py      the shape per main category, json_schema() and parse()

Callers use the names below, not the modules.
"""

from .answer import (
    ANALYSIS_MODELS,
    TRANSLATED_LISTS,
    json_schema,
    parse,
    text_fields,
    text_model,
)
from .school import SchoolAnalysis, SchoolText
from .shared import (
    CHOICES_MARKER,
    LOCALES,
    MISSING_DATA,
    PLACEHOLDER_PROS_CON_TYPES,
    UNKNOWN_SERVER_REGION,
    BadgeIn,
    BaseAnalysis,
    BaseText,
    FaqIn,
    ProsConIn,
    SafetyText,
    SpecIn,
    SubCategoryIn,
)
from .tech import DataCategoryIn, TechAnalysis, TechText
from .toys import LearningBadgeIn, ToysAnalysis, ToysText

__all__ = [
    "ANALYSIS_MODELS",
    "CHOICES_MARKER",
    "LOCALES",
    "MISSING_DATA",
    "PLACEHOLDER_PROS_CON_TYPES",
    "TRANSLATED_LISTS",
    "UNKNOWN_SERVER_REGION",
    "BadgeIn",
    "BaseAnalysis",
    "BaseText",
    "DataCategoryIn",
    "FaqIn",
    "LearningBadgeIn",
    "ProsConIn",
    "SafetyText",
    "SchoolAnalysis",
    "SchoolText",
    "SpecIn",
    "SubCategoryIn",
    "TechAnalysis",
    "TechText",
    "ToysAnalysis",
    "ToysText",
    "json_schema",
    "parse",
    "text_fields",
    "text_model",
]
