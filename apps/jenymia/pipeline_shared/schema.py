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
the main category (one of three, set on the order).
"""

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any, Generic, TypeVar

import pycountry
from django.utils.text import slugify
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from apps.jenymia.models import DataCategory, MainCategory, ProductProsCon

logger = logging.getLogger(__name__)

# PositiveSmallIntegerField is a PostgreSQL smallint; anything above this
# would be a DataError that costs the whole run.
SMALLINT_MAX = 32767


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

    use_enum_values makes a field typed as a Django TextChoices come back as
    the plain string, so parse() keeps returning data services can hand to a
    model constructor without knowing about pydantic.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    @model_validator(mode="before")
    @classmethod
    def _cut_overlong_strings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        cut = dict(data)
        for name, field in cls.model_fields.items():
            value = cut.get(name)
            # In pydantic v2 a constraint lives in the field metadata, not on
            # the field itself.
            limit = next(
                (m.max_length for m in field.metadata if hasattr(m, "max_length")),
                None,
            )
            # A number where text was asked for has to become text here, not
            # in a field validator: those run after this one, so the cut below
            # would never see it. A 17-digit GTIN sent as an int would be
            # rejected while the same value as a string is quietly cut.
            if (
                limit
                and not isinstance(value, bool)
                and isinstance(value, (int, float, Decimal))
            ):
                value = str(value)
            if not isinstance(value, str):
                continue
            # Strip first and always write the stripped value back. pydantic
            # measures the raw string, so a value that is exactly at the limit
            # plus a trailing newline would be rejected - and a rejection
            # costs the whole analysis, both languages included.
            trimmed = value.strip()
            if limit and len(trimmed) > limit:
                logger.warning(
                    "Cut %s from %s to %s characters.", name, len(trimmed), limit
                )
                trimmed = trimmed[:limit]
            if trimmed != cut.get(name):
                cut[name] = trimmed
        return cut


# DecimalField(max_digits=10, decimal_places=2) holds up to 99999999.99.
PRICE_CEILING = Decimal(100_000_000)


def _to_decimal(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        price = Decimal(str(value).replace(",", ".").strip())
    except InvalidOperation:
        logger.warning("Dropping unparsable price %r", value)
        return None
    # is_finite() first: comparing a NaN with < raises InvalidOperation.
    if not price.is_finite():
        logger.warning("Dropping implausible price %r", value)
        return None
    # Round to the column's two decimals before checking the ceiling. Django
    # hands the Decimal through untouched, so 99999999.999 would pass the
    # check and PostgreSQL would then round it to 100000000.00 and refuse it.
    try:
        price = price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        # Above ~1e26 the result needs more digits than the decimal context
        # allows. InvalidOperation is an ArithmeticError, not a ValueError,
        # so pydantic would not turn it into a validation error - it would
        # escape and take the whole analysis with it.
        logger.warning("Dropping implausible price %r", value)
        return None
    if not (0 <= price < PRICE_CEILING):
        logger.warning("Dropping implausible price %r", value)
        return None
    return price


Price = Annotated[Decimal | None, BeforeValidator(_to_decimal)]


def _to_slug(limit: int):
    """A slug from the model is free text until proven otherwise.

    The cut happens after slugify, not before: slugify normalises to NFKD,
    which expands ligatures and compatibility characters, so it can make a
    string longer. Sixty characters of "ffi" become 180 - enough to overflow
    a 60-character column that the length check had just approved.
    """

    def convert(value: str) -> str:
        # strip("-_") like slugify itself, so a cut that lands on either
        # separator does not leave it at the end of a public URL.
        return slugify(value)[:limit].strip("-_")

    return convert


Slug60 = Annotated[str, AfterValidator(_to_slug(60))]
Slug120 = Annotated[str, AfterValidator(_to_slug(120))]


# Codes language models write that ISO 3166-1 does not know.
COUNTRY_ALIASES = {"UK": "GB", "EL": "GR"}


def _to_country(value: str) -> str:
    """Only a real ISO 3166-1 alpha-2 code survives.

    Truncating is the one thing this must not do: "Germany" cut to two
    characters is "GE", which is Georgia - a wrong country stored without any
    error. An unusable value becomes empty and is logged, and the country is
    filled in by hand in the admin.
    """
    code = value.strip().upper()
    if not code:
        return ""
    code = COUNTRY_ALIASES.get(code, code)
    if len(code) != 2 or pycountry.countries.get(alpha_2=code) is None:
        logger.warning("Dropping unusable country code %r", value)
        return ""
    return code


Country = Annotated[str, AfterValidator(_to_country)]


def _to_text(value: Any) -> Any:
    """A number or a null where text was asked for is not worth the whole
    analysis.

    Models answer 10913 for a model name and 4008496123456 for a GTIN often
    enough, and pydantic coerces neither of those, nor None, to str. Rule 1
    of the prompt forbids null for a text field - a rule that only exists
    because models break it.
    """
    if value is None or isinstance(value, bool):
        return ""
    return str(value) if isinstance(value, (int, float, Decimal)) else value


Text = Annotated[str, BeforeValidator(_to_text)]


def _to_server_region(value: Any) -> Any:
    """Nothing said about hosting is what "unknown" is for. The column allows
    an empty string, the enum does not, and rejecting would cost both
    languages of a finished analysis.

    A case variant is the same answer and is accepted. Anything else is left
    alone and rejected: mapping an unrecognised region to "unknown" would
    store a claim nobody made.
    """
    if value in (None, ""):
        return DataCategory.ServerRegion.UNKNOWN.value
    if isinstance(value, str):
        wanted = value.strip().lower()
        for member in DataCategory.ServerRegion:
            if wanted == member.value.lower():
                return member.value
    return value


ServerRegion = Annotated[DataCategory.ServerRegion, BeforeValidator(_to_server_region)]

T = TypeVar("T", bound=BaseModel)


class Translated(BaseModel, Generic[T]):
    """The same block once per language. Both are required: a product that
    exists in only one language cannot be published."""

    model_config = ConfigDict(extra="forbid")

    de: T
    en: T


# --- repeating blocks -----------------------------------------------------


class SubCategoryTextDe(Block):
    slug: Slug120 = Field(
        description="German URL segment, lower case, max 120 characters, e.g. 'holz-stapelspielzeug'."
    )
    name: str = Field(
        max_length=100, description="German display name of the sub-category."
    )


class SubCategoryTextEn(Block):
    slug: Slug120 = Field(
        description=(
            "English URL segment: the translation of the German slug, never a "
            "copy of it. Lower case, max 120 characters, e.g. "
            "'wooden-stacking-toy' for 'holz-stapelspielzeug'."
        )
    )
    name: str = Field(
        max_length=100, description="English display name of the sub-category."
    )


class SubCategoryTranslations(BaseModel):
    """Like Translated, but with a separate block per language.

    The sub-category slug is the only one that exists once per locale, so it
    is also the only one that has to be translated. A shared block would show
    the model the German example under the English key, which is how German
    slugs ended up in the English rows.
    """

    model_config = ConfigDict(extra="forbid")

    de: SubCategoryTextDe
    en: SubCategoryTextEn


class SubCategoryIn(Block):
    """The main category is already set on the order and must not be repeated.
    Propose narrow, reusable groups; an existing sub-category with the same
    German slug is reused instead of created twice."""

    translations: SubCategoryTranslations


class BadgeText(Block):
    name: str = Field(
        max_length=100, description="Name of the mark, e.g. 'CE-Kennzeichnung'."
    )
    description: str = Field(default="", description="One sentence on what it means.")


class BadgeIn(Block):
    """Test marks and properties proven by a source. Reuse an existing slug
    whenever the mark is the same one."""

    slug: Slug60 = Field(
        description="Stable lower case slug, max 60 characters, e.g. 'ce', 'gs', 'fsc', 'bpa-free'."
    )
    translations: Translated[BadgeText]


class LearningBadgeText(Block):
    name: str = Field(
        max_length=100, description="Name of the area, e.g. 'Feinmotorik'."
    )


class LearningBadgeIn(Block):
    """Areas of development the product supports. Same shape as a badge, but
    LearningBadgeTranslation carries only a name."""

    slug: Slug60 = Field(
        description=(
            "Stable lower case slug, max 60 characters. Use these whenever they "
            "fit: coordination, logic, creativity, language, fine-motor, "
            "social-emotional, concentration."
        )
    )
    translations: Translated[LearningBadgeText]


class SpecText(Block):
    value: Text = Field(
        max_length=300, description="The value in this language, e.g. 'Holz'."
    )


class SpecIn(Block):
    key: Text = Field(
        max_length=60, description="Untranslated, stable key, e.g. 'material'."
    )
    sort_order: int = Field(default=0, ge=0, le=SMALLINT_MAX)
    translations: Translated[SpecText]


class FaqText(Block):
    question: str = Field(
        max_length=300, description="A question parents actually ask."
    )
    answer: str = Field(description="Two to four sentences.")


class FaqIn(Block):
    sort_order: int = Field(default=0, ge=0, le=SMALLINT_MAX)
    translations: Translated[FaqText]


class ProsConText(Block):
    text: str = Field(
        max_length=300, description="One advantage or disadvantage, in a few words."
    )


class ProsConIn(Block):
    # The model choices are the type, so the JSON schema carries the same
    # enum the database enforces - the language model cannot produce a value
    # the column would reject.
    type: ProductProsCon.Type = Field(description="Either 'pro' or 'con'.")
    sort_order: int = Field(default=0, ge=0, le=SMALLINT_MAX)
    translations: Translated[ProsConText]


class DataCategoryText(Block):
    notes: str = Field(description="What is collected and why.")


class DataCategoryIn(Block):
    data_type: DataCategory.DataType = Field(
        description="Which kind of data is collected."
    )
    # "unknown" rather than an empty string: the manufacturer saying nothing
    # is itself an answer, and the choice exists for exactly that.
    server_region: ServerRegion = Field(
        # .value, not the member: pydantic validates what the model sends but
        # hands a default through untouched, and services must always see the
        # plain string a column stores.
        default=DataCategory.ServerRegion.UNKNOWN.value,
        description="Where the data is stored; 'unknown' if the manufacturer does not say.",
    )
    is_optional: bool = False
    third_party_sharing: bool | None = Field(
        default=None, description="null means the manufacturer does not say."
    )
    sort_order: int = Field(default=0, ge=0, le=SMALLINT_MAX)
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
    model_name: Text = Field(
        default="",
        max_length=120,
        description="Model designation, e.g. '10913'. Empty string if there is none.",
    )
    gtin: Text = Field(
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
    price_official: Price = Field(
        default=None,
        description=(
            "The manufacturer's own list price in EUR as a decimal string, e.g. "
            "'29.99', taken from the manufacturer page. Null if no manufacturer "
            "page is among the sources. Never a shop price."
        ),
    )
    age_min_months: int | None = Field(
        default=None,
        ge=0,
        le=SMALLINT_MAX,
        description="Lower end of the suitable age in months, or null.",
    )
    age_max_months: int | None = Field(
        default=None,
        ge=0,
        le=SMALLINT_MAX,
        description="Upper end of the suitable age in months, or null.",
    )
    usage_lifespan_months: int | None = Field(
        default=None,
        ge=0,
        le=SMALLINT_MAX,
        description="How many months it stays useful as the child grows.",
    )
    manufactured_in_country: Country = Field(
        default="",
        description=(
            "Country of manufacture as a two-letter ISO 3166-1 alpha-2 code, e.g. "
            "'DE', 'CN', 'DK'. Never the country name. Empty string if no source "
            "states it."
        ),
    )

    sub_categories: list[SubCategoryIn] = []
    badges: list[BadgeIn] = []
    specs: list[SpecIn] = []
    faqs: list[FaqIn] = []
    pros_cons: list[ProsConIn] = []

    translations: Translated[BaseText]

    @model_validator(mode="after")
    def _repair(self):
        """Fix what the database would refuse, instead of losing the analysis.

        Both cases cost one field, not the whole run: the rest of the answer
        is usually fine, and what is missing is filled in by hand in the
        admin.
        """
        if (
            self.age_min_months is not None
            and self.age_max_months is not None
            and self.age_min_months > self.age_max_months
        ):
            logger.warning(
                "Dropping impossible age range %s-%s months.",
                self.age_min_months,
                self.age_max_months,
            )
            self.age_min_months = None
            self.age_max_months = None

        # One key per product is a database constraint; a second "material"
        # would abort the whole insert.
        seen: set[str] = set()
        kept = []
        for spec in self.specs:
            if spec.key in seen:
                logger.warning("Dropping duplicate spec key %r.", spec.key)
                continue
            seen.add(spec.key)
            kept.append(spec)
        self.specs = kept
        return self


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
    MainCategory.TOYS_LEARNING: ToysAnalysis,
    MainCategory.SCHOOL_EVERYDAY: SchoolAnalysis,
    MainCategory.TECH_SAFETY: TechAnalysis,
}


def json_schema(pipeline: str) -> dict[str, Any]:
    """The shape of the answer, for the prompt and for the provider's
    structured output mode."""
    print("SCHEMA.JSON_SCHEMA - STARTED", pipeline)
    result = ANALYSIS_MODELS[pipeline].model_json_schema()
    print("SCHEMA.JSON_SCHEMA - DONE", pipeline)
    return result


def parse(payload: str | dict[str, Any], pipeline: str) -> dict[str, Any]:
    """Validate the answer and hand back plain data.

    Raises pydantic.ValidationError if a field is missing or unusable, before
    anything is written. Value ranges that the database also guards are left
    to the database, which catches them no matter which code path wrote them.

    Returns a dict and not the model: services is the write interface for the
    whole app and must not depend on pipeline types.
    """
    print("SCHEMA.PARSE - STARTED", pipeline)
    model = ANALYSIS_MODELS[pipeline]
    result = (
        model.model_validate_json(payload)
        if isinstance(payload, str)
        else model.model_validate(payload)
    )
    print("SCHEMA.PARSE - DONE", pipeline)
    return result.model_dump()
