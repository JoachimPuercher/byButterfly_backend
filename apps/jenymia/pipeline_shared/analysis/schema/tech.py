"""What the analysis returns for Tech & Sicherheit (tech_safety), on top of
the shared part: whether the device works offline or needs an account, the
data protection texts, and the kinds of data it collects."""

from typing import Annotated

from pydantic import BeforeValidator, Field, model_validator

from .shared import (
    LOCALES,
    MISSING_DATA,
    SMALLINT_MAX,
    UNKNOWN_SERVER_REGION,
    BaseAnalysis,
    BaseText,
    ChoiceSlug,
    Flag,
    FlatTranslations,
    OptionalFlag,
    SortOrder,
    Text,
    Translated,
    _valid_entries,
    choices_from,
    logger,
)


class DataCategoryIn(FlatTranslations):
    TRANSLATED = ("notes",)

    data_type: ChoiceSlug = Field(
        description="Which kind of data is collected.",
        json_schema_extra=choices_from("data_type"),
    )
    # "unknown" rather than empty: the manufacturer saying nothing is itself
    # an answer, and the list has a value for exactly that.
    server_region: Text = Field(
        default=UNKNOWN_SERVER_REGION,
        description="Where the data is stored; 'unknown' if the manufacturer does not say.",
        json_schema_extra=choices_from("server_region"),
    )
    is_optional: Flag = False
    third_party_sharing: OptionalFlag = Field(
        default=None, description="null means the manufacturer does not say."
    )
    sort_order: SortOrder = Field(default=0, ge=0, le=SMALLINT_MAX)

    # The data type is what the entry is about, so missing notes keep it:
    # the column may not be blank, so they become the placeholder.
    @model_validator(mode="after")
    def _fill_empty_notes(self):
        for locale in LOCALES:
            name = f"notes_{locale}"
            if not getattr(self, name).strip():
                logger.warning("No %s in the answer; writing the placeholder.", name)
                setattr(self, name, MISSING_DATA[locale])
        return self

    notes_de: Text = Field(
        default="", description="What is collected and why, in German."
    )
    notes_en: Text = Field(default="", description="The same in English.")


# A list is kept entry by entry: a broken entry is dropped, not the analysis.
DataCategoryList = Annotated[
    list[DataCategoryIn], BeforeValidator(_valid_entries(DataCategoryIn))
]


class TechText(BaseText):
    privacy_short: Text = Field(
        default="",
        description=(
            "Two sentences on data protection, naming what is collected and "
            "where it is stored, or saying plainly that the maker does not say."
        ),
    )
    privacy_long: Text = Field(
        default="",
        description=(
            "The detailed data protection assessment, 120 to 250 words: which "
            "data, which server region, whether it can be switched off, "
            "whether an account is required. Silence from the maker is "
            "reported as silence, not as a no."
        ),
    )


class TechAnalysis(BaseAnalysis):
    is_offline_capable: Flag = Field(
        default=False, description="true if it works without an internet connection."
    )
    requires_account: Flag = Field(
        default=False, description="true if an account is needed to use it."
    )
    data_categories: DataCategoryList = []
    translations: Translated[TechText]
