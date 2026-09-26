"""What the analysis returns for Spielen & Lernen (toys_learning), on top of
the shared part: the child certification and the safety texts it shares
with school, how long a toy grows with the child, and the areas of
development it supports."""

from typing import Annotated

from pydantic import BeforeValidator, Field

from .shared import (
    CHILD_CERTIFIED,
    BaseAnalysis,
    Flag,
    FlatTranslations,
    RequiredText,
    SafetyText,
    Slug60,
    Text,
    Translated,
    _valid_entries,
)


class LearningBadgeIn(FlatTranslations):
    """Areas of development the product supports. LearningBadgeTranslation
    carries only a name."""

    TRANSLATED = ("name",)

    slug: Slug60 = Field(
        description=(
            "Stable lower case English slug, max 60 characters. Use these "
            "whenever they fit: coordination, logic, creativity, language, "
            "fine-motor, social-emotional, concentration. Never German."
        )
    )
    name_de: RequiredText = Field(
        max_length=100, description="German name of the area, e.g. 'Feinmotorik'."
    )
    name_en: RequiredText = Field(
        max_length=100,
        description="English name of the area, e.g. 'Fine motor skills'.",
    )


# A list is kept entry by entry: a broken entry is dropped, not the analysis.
LearningBadgeList = Annotated[
    list[LearningBadgeIn], BeforeValidator(_valid_entries(LearningBadgeIn))
]


class ToysText(SafetyText):
    growth_info: Text = Field(
        default="",
        description=(
            "80 to 150 words on how long the product grows with the child: "
            "from which age it works, when it stops being played with, and "
            "what happens to it afterwards."
        ),
    )


class ToysAnalysis(BaseAnalysis):
    is_child_certified: Flag = CHILD_CERTIFIED
    # Not a closed vocabulary yet: services creates a slug it does not know.
    # The seven established ones are named in the field description, and a new
    # one is reviewed in the admin before the product goes public.
    learning_badges: LearningBadgeList = []
    translations: Translated[ToysText]
