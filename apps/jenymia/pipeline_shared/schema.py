"""What the analysis has to return.

One definition, three uses: the JSON schema handed to the language model, the
validation of its answer, and the coercion of the values it gets wrong in
predictable ways. The prompts/ folder holds the wording, this file holds the
shape - changing which fields are collected should not mean editing prose.

The field names are the column names of the models, and the dict that parse()
returns is exactly what services.create_product_from_analysis expects. The
answer is one object: product fields at the top level, `translations` with one
block per locale, and the list blocks next to them.

Not part of the answer, on purpose: the public source list (built from the
order's own URLs, see services), images and affiliate links (entered by hand),
the main category (set on the order).
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Generic, TypeVar

from django.utils.text import slugify
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from apps.jenymia.models import Pipeline

logger = logging.getLogger(__name__)


# --- building blocks ------------------------------------------------------


class Block(BaseModel):
    """Base for every part of the answer.

    Extra keys are forbidden, which does two things at once: an answer with
    invented fields is rejected instead of silently trimmed, and the emitted
    JSON schema carries additionalProperties=false, which Claude requires for
    its structured output mode.

    max_length on a field is the real column limit and is passed to the model
    as guidance, but it is not enforced as a rejection: the model overshoots a
    character budget routinely, and losing a whole analysis over two
    characters is worse than a shortened sentence. So overlong strings are cut
    here, before validation, and the cut is logged.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _cut_overlong_strings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        cut = dict(data)
        for name, field in cls.model_fields.items():
            # In pydantic v2 a constraint lives in the field metadata, not on
            # the field itself.
            limit = next(
                (m.max_length for m in field.metadata if hasattr(m, "max_length")), None
            )
            value = cut.get(name)
            if limit and isinstance(value, str) and len(value.strip()) > limit:
                logger.warning(
                    "Cut %s from %s to %s characters.", name, len(value.strip()), limit
                )
                cut[name] = value.strip()[:limit]
        return cut


def _to_decimal(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ".").strip())
    except InvalidOperation:
        logger.warning("Dropping unparsable price %r", value)
        return None


Price = Annotated[Decimal | None, BeforeValidator(_to_decimal)]

# A slug from the model is free text until proven otherwise.
Slug = Annotated[str, AfterValidator(slugify)]

Country = Annotated[str, AfterValidator(lambda value: value.strip().upper()[:2])]

T = TypeVar("T", bound=BaseModel)


class Translated(BaseModel, Generic[T]):
    """The same block once per language. Both are required: a product that
    exists in only one language cannot be published."""

    model_config = ConfigDict(extra="forbid")

    de: T
    en: T


# --- repeating blocks -----------------------------------------------------


class CategoryText(Block):
    slug: Slug = Field(
        description="URL segment, lower case, e.g. 'holz-stapelspielzeug'."
    )
    name: str = Field(max_length=100, description="Display name of the sub-category.")


class CategoryIn(Block):
    """The main category is already set on the order and must not be repeated.
    Propose narrow, reusable groups; an existing sub-category with the same
    German slug is reused instead of created twice."""

    translations: Translated[CategoryText]


class BadgeText(Block):
    name: str = Field(
        max_length=100, description="Name of the mark, e.g. 'CE-Kennzeichnung'."
    )
    description: str = Field(default="", description="One sentence on what it means.")


class BadgeIn(Block):
    """Test marks and properties proven by a source. Reuse an existing slug
    whenever the mark is the same one."""

    slug: Slug = Field(
        description="Stable lower case slug, e.g. 'ce', 'gs', 'fsc', 'bpa-free'."
    )
    translations: Translated[BadgeText]


class LearningBadgeText(Block):
    name: str = Field(
        max_length=100, description="Name of the area, e.g. 'Feinmotorik'."
    )


class LearningBadgeIn(Block):
    """Areas of development the product supports. Same shape as a badge, but
    LearningBadgeTranslation carries only a name."""

    slug: Slug = Field(
        description=(
            "Stable lower case slug. Use these whenever they fit: coordination, "
            "logic, creativity, language, fine-motor, social-emotional, "
            "concentration."
        )
    )
    translations: Translated[LearningBadgeText]


class SpecText(Block):
    value: str = Field(
        max_length=300, description="The value in this language, e.g. 'Holz'."
    )


class SpecIn(Block):
    key: str = Field(
        max_length=60, description="Untranslated, stable key, e.g. 'material'."
    )
    sort_order: int = 0
    translations: Translated[SpecText]


class FaqText(Block):
    question: str = Field(
        max_length=300, description="A question parents actually ask."
    )
    answer: str = Field(description="Two to four sentences.")


class FaqIn(Block):
    sort_order: int = 0
    translations: Translated[FaqText]


class ProsConText(Block):
    text: str = Field(
        max_length=300, description="One advantage or disadvantage, in a few words."
    )


class ProsConIn(Block):
    type: str = Field(pattern="^(pro|con)$", description="Either 'pro' or 'con'.")
    sort_order: int = 0
    translations: Translated[ProsConText]


class DataCategoryText(Block):
    notes: str = Field(description="What is collected and why.")


class DataCategoryIn(Block):
    data_type: str = Field(
        description=(
            "One of location, audio, video, contacts, usage_stats, biometrics, "
            "messages, photos."
        )
    )
    server_region: str = Field(
        default="",
        description="One of EU, US, CN, third_country, unknown, on_device_only.",
    )
    is_optional: bool = False
    third_party_sharing: bool | None = Field(
        default=None, description="null means the manufacturer does not say."
    )
    sort_order: int = 0
    translations: Translated[DataCategoryText]


# --- text per language ----------------------------------------------------


class BaseText(Block):
    title: str = Field(
        max_length=200,
        description=(
            "Product name without brand and without model designation, e.g. "
            "'Duplo Steinebox'. The page shows brand, title and model together."
        ),
    )
    hook: str = Field(
        max_length=300, description="One sentence that makes a parent read on."
    )
    description_short: str = Field(
        description="Two to three sentences for the product card."
    )
    description_detail: str = Field(description="The full review text.")
    meta_title: str = Field(max_length=70, description="SEO title, 50-60 characters.")
    meta_description: str = Field(
        max_length=180, description="SEO description, 150-160 characters."
    )
    summary: str = Field(
        description=(
            "40-60 words, a complete answer on its own, understandable without "
            "the rest of the page. This is the block AI search engines quote."
        )
    )
    verdict: str = Field(
        max_length=300, description="One quotable sentence with the verdict."
    )
    question_headline: str = Field(
        max_length=200,
        description="The headline phrased as the question a parent would type.",
    )


class SafetyText(BaseText):
    safety_short: str = Field(description="Two sentences on safety.")
    safety_long: str = Field(description="The detailed safety assessment.")


class ToysText(SafetyText):
    growth_info: str = Field(description="How long the product grows with the child.")


class SchoolText(SafetyText):
    pass


class TechText(BaseText):
    privacy_short: str = Field(description="Two sentences on data protection.")
    privacy_long: str = Field(description="The detailed data protection assessment.")


# --- the answer -----------------------------------------------------------

CHILD_CERTIFIED = Field(
    default=False, description="true if a test mark for children's products is proven."
)


class BaseAnalysis(Block):
    """Everything every pipeline collects. The group classes below add what
    only they need and narrow the translation block."""

    brand: str = Field(
        max_length=100,
        description="Manufacturer name, exactly as written on the product.",
    )
    model_name: str = Field(
        default="",
        max_length=120,
        description="Model designation, e.g. '10913'. Empty string if there is none.",
    )
    gtin: str = Field(
        default="",
        max_length=14,
        description="EAN or UPC if it appears in the sources, otherwise an empty string.",
    )
    ampel_score: int | None = Field(
        default=None,
        ge=1,
        le=3,
        description="Overall verdict: 1 = red, 2 = yellow, 3 = green. Criteria are in the prompt.",
    )
    price_current: Price = Field(
        default=None,
        description="Current price in EUR as a decimal string, e.g. '29.99', or null.",
    )
    price_original: Price = Field(
        default=None, description="List price before discount, same format, or null."
    )
    age_min_months: int | None = Field(
        default=None,
        ge=0,
        description="Lower end of the suitable age in months, or null.",
    )
    age_max_months: int | None = Field(
        default=None,
        ge=0,
        description="Upper end of the suitable age in months, or null.",
    )
    usage_lifespan_months: int | None = Field(
        default=None,
        ge=0,
        description="How many months it stays useful as the child grows.",
    )
    manufactured_in_country: Country = Field(
        default="",
        description="ISO 3166-1 alpha-2 country of manufacture, e.g. 'DE', or ''.",
    )

    categories: list[CategoryIn] = []
    badges: list[BadgeIn] = []
    specs: list[SpecIn] = []
    faqs: list[FaqIn] = []
    pros_cons: list[ProsConIn] = []

    translations: Translated[BaseText]


class ToysAnalysis(BaseAnalysis):
    is_child_certified: bool = CHILD_CERTIFIED
    # Not a closed vocabulary yet: services creates a slug it does not know.
    # The seven established ones are named in the field description, and a new
    # one is reviewed in the admin before the product goes public.
    learning_badges: list[LearningBadgeIn] = []
    translations: Translated[ToysText]


class SchoolAnalysis(BaseAnalysis):
    is_child_certified: bool = CHILD_CERTIFIED
    translations: Translated[SchoolText]


class TechAnalysis(BaseAnalysis):
    is_offline_capable: bool = Field(
        default=False, description="true if it works without an internet connection."
    )
    requires_account: bool = Field(
        default=False, description="true if an account is needed to use it."
    )
    data_categories: list[DataCategoryIn] = []
    translations: Translated[TechText]


# Indexing with an unknown pipeline raises, which is wanted: a typo must not
# produce a base-only schema that validates.
ANALYSIS_MODELS: dict[str, type[BaseAnalysis]] = {
    Pipeline.TOYS: ToysAnalysis,
    Pipeline.SCHOOL: SchoolAnalysis,
    Pipeline.TECH: TechAnalysis,
}


def json_schema(pipeline: str) -> dict[str, Any]:
    """The shape of the answer, for the prompt and for the provider's
    structured output mode."""
    return ANALYSIS_MODELS[pipeline].model_json_schema()


def parse(payload: str | dict[str, Any], pipeline: str) -> dict[str, Any]:
    """Validate the answer and hand back plain data.

    Raises pydantic.ValidationError if a field is missing or unusable, before
    anything is written. Value ranges that the database also guards are left
    to the database, which catches them no matter which code path wrote them.

    Returns a dict and not the model: services is the write interface for the
    whole app and must not depend on pipeline types.
    """
    model = ANALYSIS_MODELS[pipeline]
    result = (
        model.model_validate_json(payload)
        if isinstance(payload, str)
        else model.model_validate(payload)
    )
    return result.model_dump()
