"""What the analysis returns - the part every product group shares.

One definition, three uses: the JSON schema handed to the language model, the
validation of its answer, and the coercion of the values it gets wrong in
predictable ways. The prompts/ folder holds the wording, the schema holds
the shape - changing which fields are collected should not mean editing
prose.

This file holds the building blocks (field types with their fallbacks, the
list and translation machinery), the lists and texts every group has, and
BaseAnalysis, the answer every group extends. What only one group has is in
toys.py, school.py and tech.py; answer.py puts them together per main
category. The field names are the column names of the models.

Not part of the answer, on purpose: the public source list (built from the
order's own URLs, see services), images and affiliate links (entered by hand),
the main category (one of three, set on the order).
"""

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any, ClassVar, Generic, TypeVar

import pycountry
from django.utils.text import slugify
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticUndefined

logger = logging.getLogger(__name__)

# PositiveSmallIntegerField is a PostgreSQL smallint; anything above this
# would be a DataError that costs the whole run.
SMALLINT_MAX = 32767

# Every field is marked required in the schema the model gets (see
# json_schema), so it writes all of them. Here nearly every field has a
# fallback instead: a missing or unusable value costs that field, not the
# whole analysis. Each fallback is logged. Where the database column must not
# be empty, the fallback is this placeholder - visible in the admin, where an
# empty field is not - in the language of the row it goes into.
MISSING_DATA = {
    "de": "Zu wenige Analysedaten - manuell eintragen",
    "en": "Not enough analysis data - enter manually",
}
# German is the original, English the translation of the same statements.
LOCALES = ("de", "en")
MAX_FAQS = 8
# Per type, not over the whole list.
MAX_PROS_CONS_PER_TYPE = 6
# The slugs of ProsConType the placeholder rows use when no pros or cons
# survive - one of each, because a page shows both lists.
PLACEHOLDER_PROS_CON_TYPES = ("pro", "con")


# --- building blocks ------------------------------------------------------


class Block(BaseModel):
    """Base for every part of the answer.

    The emitted JSON schema carries additionalProperties=false, so the model
    is told that no other keys exist. It still invents one now and then
    (a "title_placeholder": null next to the real title), because the tool
    input is not generated under a grammar. Such a key is dropped before
    validation and logged: it holds nothing the database has a column for,
    and rejecting it would cost the whole analysis.

    max_length on a field is the real column limit and is passed to the model
    as guidance, but it is not enforced as a rejection: the model overshoots a
    character budget routinely, and losing a whole analysis over two
    characters is worse than a shortened sentence. So overlong strings are cut
    here, before validation, and the cut is logged.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _drop_unknown_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        unknown = set(data) - set(cls.model_fields)
        if not unknown:
            return data
        logger.warning(
            "Dropping unknown keys %s from %s.", sorted(unknown), cls.__name__
        )
        return {key: value for key, value in data.items() if key not in unknown}

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


def _to_country(value: Any) -> str:
    """Only a real ISO 3166-1 alpha-2 code survives.

    Truncating is the one thing this must not do: "Germany" cut to two
    characters is "GE", which is Georgia - a wrong country stored without any
    error. An unusable value becomes empty and is logged, and the country is
    filled in by hand in the admin.
    """
    if not isinstance(value, str):
        if value is not None:
            logger.warning("Dropping unusable country code %r", value)
        return ""
    code = value.strip().upper()
    if not code:
        return ""
    code = COUNTRY_ALIASES.get(code, code)
    if len(code) != 2 or pycountry.countries.get(alpha_2=code) is None:
        logger.warning("Dropping unusable country code %r", value)
        return ""
    return code


Country = Annotated[str, BeforeValidator(_to_country)]


def _to_text(value: Any) -> Any:
    """A number or a null where text was asked for is not worth the whole
    analysis.

    Models answer 10913 for a model name and 4008496123456 for a GTIN often
    enough, and pydantic coerces neither of those, nor None, to str. Rule 1
    of the prompt forbids null for a text field - a rule that only exists
    because models break it. A list or an object is no text at all and
    becomes empty.
    """
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if not isinstance(value, str):
        logger.warning("Dropping %s where text was expected.", type(value).__name__)
        return ""
    return value


Text = Annotated[str, BeforeValidator(_to_text)]


def _to_required_text(value: Any) -> str:
    """Like Text, but empty is refused. Used for the one field that makes a
    list entry what it is (the question of a FAQ, the name of a badge): an
    entry without it is dropped as a whole, see _valid_entries."""
    text = _to_text(value)
    if not text.strip():
        raise ValueError("must not be empty")
    return text


RequiredText = Annotated[str, BeforeValidator(_to_required_text)]


def _bounded_int(low: int, high: int, fallback: int | None):
    """A whole number within the column's range, or the fallback.

    Accepts what models write for a number: 3, "3", 3.0. Anything else -
    "12 Monate", 2.5, a value outside the range the database enforces - is
    logged and replaced, because the whole analysis is worth more than one
    number that can be entered by hand.
    """

    def convert(value: Any) -> int | None:
        if value is None or value == "":
            return fallback
        if isinstance(value, bool):
            logger.warning("Replacing unusable number %r with %r.", value, fallback)
            return fallback
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            logger.warning("Replacing unusable number %r with %r.", value, fallback)
            return fallback
        if (
            not number.is_finite()
            or number != number.to_integral_value()
            or not low <= number <= high
        ):
            logger.warning("Replacing unusable number %r with %r.", value, fallback)
            return fallback
        return int(number)

    return convert


Ampel = Annotated[int | None, BeforeValidator(_bounded_int(1, 3, None))]
Months = Annotated[int | None, BeforeValidator(_bounded_int(0, SMALLINT_MAX, None))]
SortOrder = Annotated[int, BeforeValidator(_bounded_int(0, SMALLINT_MAX, 0))]

TRUE_WORDS = {"true", "yes", "ja", "1"}
FALSE_WORDS = {"false", "no", "nein", "0"}


def _to_flag(fallback: bool | None):
    """true or false, whichever way the model spells it; anything else
    becomes the fallback and is logged. For a plain yes/no column the
    fallback is False, the column default. For third_party_sharing it is
    None, which there means "not stated"."""

    def convert(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if value is None:
            if fallback is not None:
                logger.warning("Replacing null flag with %r.", fallback)
            return fallback
        word = str(value).strip().lower()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
        logger.warning("Replacing unusable flag %r with %r.", value, fallback)
        return fallback

    return convert


Flag = Annotated[bool, BeforeValidator(_to_flag(False))]
OptionalFlag = Annotated[bool | None, BeforeValidator(_to_flag(None))]


# Marker on a field whose allowed values are a choice list in the database.
# json_schema() replaces it with the list's current slugs as an enum; the
# write step (services) resolves the slug against the same table.
CHOICES_MARKER = "x-choices"


def choices_from(list_name: str) -> dict[str, str]:
    """json_schema_extra for a field that takes a slug of the named list."""
    return {CHOICES_MARKER: list_name}


# A slug of a choice list. Which slugs exist is the database's business:
# services matches it case-insensitively, so "Pro" still finds "pro", and a
# slug that is in no list is dropped there. Empty is refused, so an entry
# without its type is dropped as a whole (see _valid_entries).
ChoiceSlug = Annotated[str, BeforeValidator(_to_required_text)]

# Nothing said about hosting is what "unknown" is for; services also falls
# back to it for a region the list does not know.
UNKNOWN_SERVER_REGION = "unknown"


def _valid_entries(entry_model: type[BaseModel]):
    """Keep a list entry by entry.

    One broken entry - a FAQ without its answer, a pros/cons point whose type
    is in no list - is dropped and logged, and the rest of the list survives
    instead of the whole analysis being rejected. A list that ends up empty
    is handled by _repair where the page needs a row, and null or anything
    that is not a list counts as empty. The validation context (order facts,
    choice lists) is handed on to every entry.
    """

    def convert(value: Any, info: ValidationInfo) -> list:
        if value is None:
            return []
        if not isinstance(value, list):
            logger.warning(
                "Dropping %s list: got %s.", entry_model.__name__, type(value).__name__
            )
            return []
        kept = []
        for entry in value:
            try:
                kept.append(entry_model.model_validate(entry, context=info.context))
            except ValidationError as error:
                logger.warning(
                    "Dropping unusable %s entry: %s",
                    entry_model.__name__,
                    "; ".join(
                        f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                        for e in error.errors()
                    ),
                )
        return kept

    return convert


T = TypeVar("T", bound=BaseModel)


class Translated(BaseModel, Generic[T]):
    """The same block once per language. Both belong in every analysis: a
    product that exists in only one language cannot be published.

    A language that arrived without a block becomes an empty one, which is
    then filled with fallbacks - the product is saved with placeholders
    instead of being lost. The placeholder is written in the language of its
    block, so no German reaches an English row. Only the German title falls
    back to the one entered on the order, which is German; the English title
    is the translation's or the English placeholder. A language the site
    does not have is dropped.
    """

    model_config = ConfigDict(extra="forbid")

    de: T
    en: T

    @model_validator(mode="after")
    def _fill_placeholders(self, info: ValidationInfo):
        german = self.de
        if "title" in type(german).model_fields and not german.title.strip():
            german.title = (info.context or {}).get("title", "")
            logger.warning("No de title in the answer; using %r.", german.title)
        for locale in LOCALES:
            block = getattr(self, locale)
            for name in getattr(block, "REQUIRED_TEXT", ()):
                if not getattr(block, name).strip():
                    logger.warning(
                        "No %s %s in the answer; writing the placeholder.", locale, name
                    )
                    setattr(block, name, MISSING_DATA[locale])
        return self

    @model_validator(mode="before")
    @classmethod
    def _fill_missing_locales(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            logger.warning("Replacing unusable translations %r.", type(data).__name__)
            data = {}
        unknown = set(data) - set(LOCALES)
        if unknown:
            logger.warning("Dropping unknown locales %s.", sorted(unknown))
        filled = {}
        for locale in LOCALES:
            block = data.get(locale)
            if not isinstance(block, dict):
                logger.warning("No usable %r text block; using fallbacks.", locale)
                block = {}
            filled[locale] = block
        return filled


class FlatTranslations(Block):
    """A list entry whose translated fields are flat <field>_<locale> pairs.

    The model writes value_de and value_en side by side instead of a nested
    block per language. Nested blocks are where it put values under the wrong
    key and dropped a whole language. The write layer still expects
    translations={"de": {...}, "en": {...}}, so model_dump rebuilds that
    shape here, once for every list entry, and services stays unchanged.
    """

    # Names of the fields that exist once per locale, without the suffix.
    TRANSLATED: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _check_choices(self, info: ValidationInfo):
        """A choice field must name a value of its list - the same slugs the
        model was offered (json_schema), passed in as context by parse().
        Matched case-insensitively and stored as the list spells it. A field
        with a default falls back to it; one without drops the entry, so the
        placeholders in _repair still apply when nothing usable is left."""
        offered = (info.context or {}).get("choices", {})
        for name, field in type(self).model_fields.items():
            list_name = (field.json_schema_extra or {}).get(CHOICES_MARKER)
            if list_name is None or list_name not in offered:
                continue
            by_lower = {slug.lower(): slug for slug in offered[list_name]}
            value = getattr(self, name)
            slug = by_lower.get(value.strip().lower())
            if slug is not None:
                setattr(self, name, slug)
            elif field.default not in (None, PydanticUndefined):
                logger.warning(
                    "Replacing %r in %s with %r.", value, name, field.default
                )
                setattr(self, name, field.default)
            else:
                raise ValueError(f"{name} {value!r} is not in {list_name}")
        return self

    @model_serializer(mode="wrap")
    def _nest_translations(self, handler) -> dict[str, Any]:
        data = handler(self)
        data["translations"] = {
            locale: {name: data.pop(f"{name}_{locale}") for name in self.TRANSLATED}
            for locale in LOCALES
        }
        return data


# --- repeating blocks -----------------------------------------------------


class SubCategoryIn(FlatTranslations):
    """The main category is already set on the order and must not be repeated.
    Propose narrow, reusable groups; an existing sub-category with the same
    German slug is reused instead of created twice.

    The slug is the only one that exists once per locale: sub-category pages
    live under /<locale>/<slug>/."""

    TRANSLATED = ("slug", "name")

    slug_de: Slug120 = Field(
        description="German URL segment, lower case, max 120 characters, e.g. 'holz-stapelspielzeug'."
    )
    slug_en: Slug120 = Field(
        description=(
            "English URL segment: the translation of the German slug, never a "
            "copy of it. Lower case, max 120 characters, e.g. "
            "'wooden-stacking-toy' for 'holz-stapelspielzeug'."
        )
    )
    name_de: RequiredText = Field(max_length=100, description="German display name.")
    name_en: RequiredText = Field(max_length=100, description="English display name.")


class BadgeIn(FlatTranslations):
    """Test marks and properties proven by a source. Reuse an existing slug
    whenever the mark is the same one."""

    TRANSLATED = ("name", "description")

    slug: Slug60 = Field(
        description=(
            "Stable lower case English slug, max 60 characters, e.g. 'ce', 'gs', "
            "'fsc', 'bpa-free'. Never German."
        )
    )
    name_de: RequiredText = Field(
        max_length=100, description="German name of the mark, e.g. 'CE-Kennzeichnung'."
    )
    name_en: RequiredText = Field(
        max_length=100, description="English name of the mark, e.g. 'CE marking'."
    )
    description_de: Text = Field(
        default="", description="One German sentence on what it means."
    )
    description_en: Text = Field(
        default="", description="The same sentence in English."
    )


SPEC_VALUE = (
    "as concrete as the source is: 'Buchenholz, unbehandelt' rather than "
    "'Holz', '38 x 26 x 9 cm' rather than 'kompakt'."
)


class SpecIn(FlatTranslations):
    TRANSLATED = ("label", "value")

    key: RequiredText = Field(
        max_length=60,
        description=(
            "Untranslated, stable lower case English key, e.g. 'material', "
            "'dimensions', 'weight', 'contents', 'age-range-maker', 'care', "
            "'battery-life'. Never German. Compares products; not shown."
        ),
    )
    sort_order: SortOrder = Field(default=0, ge=0, le=SMALLINT_MAX)
    label_de: RequiredText = Field(
        max_length=100,
        description="What the page shows for this fact, in German, e.g. 'Gewicht'.",
    )
    label_en: RequiredText = Field(
        max_length=100, description="The same label in English, e.g. 'Weight'."
    )
    value_de: RequiredText = Field(
        max_length=300, description=f"German value, {SPEC_VALUE}"
    )
    value_en: RequiredText = Field(
        max_length=300, description="The same value in English."
    )


class FaqIn(FlatTranslations):
    TRANSLATED = ("question", "answer")

    sort_order: SortOrder = Field(default=0, ge=0, le=SMALLINT_MAX)
    question_de: RequiredText = Field(
        max_length=300,
        description=(
            "A question parents really type into a search engine before "
            "buying, e.g. 'Ist die Trinkflasche spuelmaschinenfest?'. Not a "
            "question the review text has already answered."
        ),
    )
    question_en: RequiredText = Field(
        max_length=300, description="The same question in English."
    )
    answer_de: RequiredText = Field(
        description=(
            "Two to four sentences that start with the answer, not with an "
            "introduction, and name the fact from the source that backs it. "
            "No sentence that would fit any other product of this kind."
        )
    )
    answer_en: RequiredText = Field(description="The same answer in English.")


class ProsConIn(FlatTranslations):
    TRANSLATED = ("text",)

    type: ChoiceSlug = Field(
        description="'pro' for an advantage, 'con' for a drawback.",
        json_schema_extra=choices_from("pros_con_type"),
    )
    sort_order: SortOrder = Field(default=0, ge=0, le=SMALLINT_MAX)
    text_de: RequiredText = Field(
        max_length=300,
        description=(
            "One advantage or drawback with the concrete reason behind it, "
            "not only the label: 'Haelt eine Schulwoche ohne Nachladen - "
            "zwei Tests messen sechs bis sieben Tage' rather than 'gute "
            "Akkulaufzeit'."
        ),
    )
    text_en: RequiredText = Field(
        max_length=300, description="The same point in English."
    )


# A list is kept entry by entry: a broken entry is dropped, not the analysis.
SubCategoryList = Annotated[
    list[SubCategoryIn], BeforeValidator(_valid_entries(SubCategoryIn))
]
BadgeList = Annotated[list[BadgeIn], BeforeValidator(_valid_entries(BadgeIn))]
SpecList = Annotated[list[SpecIn], BeforeValidator(_valid_entries(SpecIn))]
FaqList = Annotated[list[FaqIn], BeforeValidator(_valid_entries(FaqIn))]
ProsConList = Annotated[list[ProsConIn], BeforeValidator(_valid_entries(ProsConIn))]


# --- text per language ----------------------------------------------------


class BaseText(Block):
    """The long texts of one language.

    Every field falls back instead of rejecting the analysis. The texts whose
    columns may not be blank (REQUIRED_TEXT) are filled by Translated, which
    knows the language of this block. Everything else stays empty, which its
    column allows.
    """

    REQUIRED_TEXT: ClassVar[tuple[str, ...]] = (
        "title",
        "hook",
        "description_short",
        "description_detail",
    )

    title: Text = Field(
        default="",
        max_length=200,
        description=(
            "Product name without brand and without model designation, e.g. "
            "'Duplo Steinebox'. The page shows brand, title and model together."
        ),
    )
    hook: Text = Field(
        default="",
        max_length=300,
        description=(
            "One sentence that makes a parent read on, built on the one fact "
            "that decides this product, e.g. 'Drei Tests kommen auf dieselbe "
            "Schwachstelle - der Deckel.' No advertising, no superlative."
        ),
    )
    description_short: Text = Field(
        default="",
        description=(
            "Two to three sentences for the product card, in the first person "
            "plural. Must name at least one concrete fact from the sources - "
            "a measurement, a material, a test result."
        ),
    )
    description_detail: Text = Field(
        default="",
        description=(
            "The full review, 400 to 700 words in four to six paragraphs: "
            "what it is, what the sources agree on, where they contradict "
            "each other and who says what, how it holds up in everyday use, "
            "and for whom it is worth it. First person plural throughout. "
            "Every paragraph carries figures from the sources."
        ),
    )
    meta_title: Text = Field(
        default="",
        max_length=70,
        description=(
            "SEO title, 50-60 characters, starting with the term parents "
            "search for, pattern '<Produkt> Test 2026 - <kurze Frage>'."
        ),
    )
    meta_description: Text = Field(
        default="",
        max_length=180,
        description=(
            "SEO description, 150-160 characters, starting with the search "
            "term, naming what the reader learns and how many sources were "
            "reviewed."
        ),
    )
    summary: Text = Field(
        default="",
        description=(
            "40-60 words, a complete answer on its own, understandable without "
            "the rest of the page. This is the block AI search engines quote, "
            "so it names the product and the verdict inside itself and refers "
            "back to nothing - no 'dieses Produkt', no 'wie oben'."
        ),
    )
    verdict: Text = Field(
        default="",
        max_length=300,
        description=(
            "One quotable sentence that gives the verdict and the reason for "
            "it, e.g. 'Empfehlenswert fuer den taeglichen Schulweg - die "
            "Naht an den Traegern ist die einzige Stelle, die in zwei Tests "
            "nachgab.'"
        ),
    )
    question_headline: Text = Field(
        default="",
        max_length=200,
        description=(
            "The headline as the question a parent types word for word, e.g. "
            "'Haelt die Brotdose von Marke X eine Schulwoche aus?'"
        ),
    )


# Shared by the groups whose products children handle (toys, school).
class SafetyText(BaseText):
    safety_short: Text = Field(
        default="",
        description=(
            "Two sentences on safety, naming the test mark or the norm the "
            "source states, or saying plainly that no source checked it."
        ),
    )
    safety_long: Text = Field(
        default="",
        description=(
            "The detailed safety assessment, 120 to 250 words: small parts, "
            "materials, edges, batteries, what was tested by whom. What no "
            "source checked is named as unchecked, never as safe."
        ),
    )


# --- the answer -----------------------------------------------------------

# Shared by toys and school, like SafetyText.
CHILD_CERTIFIED = Field(
    default=False, description="true if a test mark for children's products is proven."
)


class BaseAnalysis(Block):
    """Everything every pipeline collects. The group classes (toys.py,
    school.py, tech.py) add what only they need and narrow the translation
    block.

    Brand and title are known from the order before the model writes a word.
    They reach this class as the validation context (see parse) and fill in
    where the answer has nothing usable, because the write step needs both.
    """

    @model_validator(mode="before")
    @classmethod
    def _fill_missing_translations(cls, data: Any) -> Any:
        if isinstance(data, dict) and "translations" not in data:
            logger.warning("No translations in the answer; using fallbacks.")
            data = {**data, "translations": {}}
        return data

    @model_validator(mode="after")
    def _fill_brand(self, info: ValidationInfo):
        if not self.brand.strip():
            self.brand = (info.context or {}).get("brand", "")
            logger.warning("No brand in the answer; using %r.", self.brand)
        return self

    brand: Text = Field(
        default="",
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
    ampel_score: Ampel = Field(
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
    age_min_months: Months = Field(
        default=None,
        ge=0,
        le=SMALLINT_MAX,
        description="Lower end of the suitable age in months, or null.",
    )
    age_max_months: Months = Field(
        default=None,
        ge=0,
        le=SMALLINT_MAX,
        description="Upper end of the suitable age in months, or null.",
    )
    usage_lifespan_months: Months = Field(
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

    sub_categories: SubCategoryList = []
    badges: BadgeList = []
    specs: SpecList = Field(
        default_factory=list,
        description=(
            "The hard facts the sources state: material, dimensions, weight, "
            "the maker's age range, what is in the box, care, battery life. "
            "One entry per fact, never a whole sentence."
        ),
    )
    faqs: FaqList = Field(
        default_factory=list,
        description="Two to eight questions parents really ask before buying.",
    )
    pros_cons: ProsConList = Field(
        default_factory=list,
        description=(
            "Two to six advantages and two to six drawbacks, each with the "
            "concrete reason from the sources. A product without a single "
            "drawback does not exist: if no source names one, that silence "
            "is the drawback."
        ),
    )

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

        self.faqs = self.faqs[:MAX_FAQS]
        if not self.faqs:
            logger.warning("No FAQ in the answer; writing the placeholder row.")
            self.faqs = [
                FaqIn.model_validate(
                    {
                        f"{name}_{locale}": MISSING_DATA[locale]
                        for name in FaqIn.TRANSLATED
                        for locale in LOCALES
                    }
                )
            ]

        # Capped per type rather than over the whole list: six advantages at
        # the front would otherwise push out every drawback behind them.
        per_type: dict[str, int] = {}
        kept_pros_cons = []
        for entry in self.pros_cons:
            # Lower case like the slug lookup in services: "Pro" is "pro".
            kind = entry.type.strip().lower()
            per_type[kind] = per_type.get(kind, 0) + 1
            if per_type[kind] > MAX_PROS_CONS_PER_TYPE:
                logger.warning("Dropping %r beyond the cap.", entry.type)
                continue
            kept_pros_cons.append(entry)
        self.pros_cons = kept_pros_cons

        if not self.pros_cons:
            logger.warning(
                "No pros or cons in the answer; writing the placeholder rows."
            )
            self.pros_cons = [
                ProsConIn.model_validate(
                    {"type": kind}
                    | {f"text_{locale}": MISSING_DATA[locale] for locale in LOCALES}
                )
                for kind in PLACEHOLDER_PROS_CON_TYPES
            ]

        if not self.specs:
            logger.warning("No specs in the answer; writing the placeholder row.")
            self.specs = [
                SpecIn.model_validate(
                    {"key": "note", "label_de": "Hinweis", "label_en": "Note"}
                    | {f"value_{locale}": MISSING_DATA[locale] for locale in LOCALES}
                )
            ]
        return self
