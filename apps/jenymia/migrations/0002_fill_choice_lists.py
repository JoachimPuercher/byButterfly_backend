"""Fills every choice list with its values and their German and English
labels: data types, server regions, availabilities, source types, the
pros/cons types, the traffic light scores, all ISO 3166-1 countries and the
three main categories.

Data only, so it is kept apart from the schema in 0001. Reversible: the way
back empties the lists again, and the database's foreign keys make that
fail loudly while a product or an order still points at one of their values.
"""

import gettext

import pycountry
from django.db import migrations

# (slug, German label, English label), in display order.
DATA_TYPES = [
    ("location", "Standortdaten", "Location data"),
    ("audio", "Audioaufnahmen", "Audio recordings"),
    ("video", "Videoaufnahmen", "Video recordings"),
    ("contacts", "Kontakte", "Contacts"),
    ("usage_stats", "Nutzungsstatistiken", "Usage statistics"),
    ("biometrics", "Biometrische Daten", "Biometric data"),
    ("messages", "Nachrichten", "Messages"),
    ("photos", "Fotos", "Photos"),
]
SERVER_REGIONS = [
    ("EU", "EU", "EU"),
    ("US", "USA", "USA"),
    ("CN", "China", "China"),
    ("third_country", "Drittland", "Third country"),
    ("on_device_only", "Nur auf dem Gerät", "On the device only"),
    ("unknown", "Unbekannt", "Unknown"),
]
AVAILABILITIES = [
    ("in_stock", "Auf Lager", "In stock"),
    ("out_of_stock", "Nicht auf Lager", "Out of stock"),
    ("preorder", "Vorbestellbar", "Available for pre-order"),
    ("discontinued", "Nicht mehr erhältlich", "Discontinued"),
]
SOURCE_TYPES = [
    ("youtube", "YouTube", "YouTube"),
    ("article", "Artikel", "Article"),
    ("test_institute", "Testinstitut", "Test institute"),
    ("manufacturer", "Hersteller", "Manufacturer"),
    ("datasheet", "Datenblatt", "Datasheet"),
    ("forum", "Forum", "Forum"),
]
# The label is the heading of the list on the page.
PROS_CON_TYPES = [
    ("pro", "Vorteile", "Pros"),
    ("con", "Nachteile", "Cons"),
]
AMPEL_SCORES = [
    (1, "Abraten", "Not recommended"),
    (2, "Mit Einschränkungen", "With limitations"),
    (3, "Empfehlenswert", "Recommended"),
]
# (slug, German name, German URL slug, English name, English URL slug). The
# slug is the product group's and the prompt file's name.
MAIN_CATEGORIES = [
    (
        "toys_learning",
        "Spielen & Lernen",
        "spielen-lernen",
        "Play & Learn",
        "play-learn",
    ),
    (
        "school_everyday",
        "Schule & Alltag",
        "schule-alltag",
        "School & Everyday",
        "school-everyday",
    ),
    (
        "tech_safety",
        "Tech & Sicherheit",
        "tech-sicherheit",
        "Tech & Safety",
        "tech-safety",
    ),
]

# (model, translation model, foreign key of the translation, values)
SLUG_LISTS = [
    ("DataType", "DataTypeTranslation", "data_type", DATA_TYPES),
    ("ServerRegion", "ServerRegionTranslation", "server_region", SERVER_REGIONS),
    ("Availability", "AvailabilityTranslation", "availability", AVAILABILITIES),
    ("SourceType", "SourceTypeTranslation", "source_type", SOURCE_TYPES),
    ("ProsConType", "ProsConTypeTranslation", "pros_con_type", PROS_CON_TYPES),
]
LIST_MODELS = [
    *(model for model, _, _, _ in SLUG_LISTS),
    "AmpelScore",
    "Country",
    "MainCategory",
]


def country_names() -> list[tuple[str, str, str]]:
    """(code, German name, English name) for every ISO 3166-1 country.

    Both from the common name where ISO has one ("South Korea" /
    "Südkorea", not "Korea, Republic of" / "Korea, Republik"), so the two
    languages name a country the same way. The few common names without a
    German translation (Iran, Laos, Taiwan, Venezuela, Vietnam) are spelled
    the same in German.
    """
    german = gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=["de"])
    names = []
    for country in pycountry.countries:
        name = getattr(country, "common_name", country.name)
        names.append((country.alpha_2, german.gettext(name), name))
    return names


def fill_lists(apps, schema_editor) -> None:
    for model_name, translation_name, fk, values in SLUG_LISTS:
        model = apps.get_model("jenymia", model_name)
        translation = apps.get_model("jenymia", translation_name)
        for position, (slug, label_de, label_en) in enumerate(values):
            row = model.objects.create(slug=slug, sort_order=position)
            translation.objects.bulk_create(
                [
                    translation(**{fk: row}, locale="de", label=label_de),
                    translation(**{fk: row}, locale="en", label=label_en),
                ]
            )

    ampel = apps.get_model("jenymia", "AmpelScore")
    ampel_translation = apps.get_model("jenymia", "AmpelScoreTranslation")
    for value, label_de, label_en in AMPEL_SCORES:
        row = ampel.objects.create(value=value)
        ampel_translation.objects.bulk_create(
            [
                ampel_translation(ampel_score=row, locale="de", label=label_de),
                ampel_translation(ampel_score=row, locale="en", label=label_en),
            ]
        )

    country = apps.get_model("jenymia", "Country")
    country_translation = apps.get_model("jenymia", "CountryTranslation")
    names = country_names()
    rows = country.objects.bulk_create([country(code=code) for code, _, _ in names])
    country_translation.objects.bulk_create(
        [
            country_translation(country=row, locale=locale, label=label)
            for row, (_, name_de, name_en) in zip(rows, names, strict=True)
            for locale, label in (("de", name_de), ("en", name_en))
        ]
    )

    category = apps.get_model("jenymia", "MainCategory")
    category_translation = apps.get_model("jenymia", "MainCategoryTranslation")
    for position, (slug, name_de, slug_de, name_en, slug_en) in enumerate(
        MAIN_CATEGORIES
    ):
        row = category.objects.create(slug=slug, sort_order=position)
        category_translation.objects.bulk_create(
            [
                category_translation(
                    main_category=row, locale="de", name=name_de, slug=slug_de
                ),
                category_translation(
                    main_category=row, locale="en", name=name_en, slug=slug_en
                ),
            ]
        )


def empty_lists(apps, schema_editor) -> None:
    """Plain DELETE statements, labels first, then the values. The ORM's
    cascading delete cannot be used here: on the way back it compares the
    list rows with model classes of another migration state and refuses. The
    PROTECT it would check is enforced by the database's foreign keys."""
    alias = schema_editor.connection.alias
    for model_name in [*(f"{name}Translation" for name in LIST_MODELS), *LIST_MODELS]:
        apps.get_model("jenymia", model_name).objects.using(alias).all()._raw_delete(
            alias
        )


class Migration(migrations.Migration):
    dependencies = [
        ("jenymia", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(fill_lists, empty_lists),
    ]
