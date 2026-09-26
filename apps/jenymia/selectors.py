"""Read access to jenymia data.

Views never touch Model.objects directly. The rule "only published products
leave the backend" is written down once, here - not in every view, where it
is forgotten exactly once and then drafts are public.
"""

from django.db.models import QuerySet

from .models import (
    DataType,
    Product,
    ProductTranslation,
    ProsConType,
    ServerRegion,
    product_group,
)

# What the detail serializer reads on every product, whatever its group.
DETAIL_SELECT = (
    "brand",
    "author",
    "primary_category",
    "ampel_score",
    "manufactured_in_country",
)
DETAIL_PREFETCH = (
    "translations",
    "group_translations",
    "author__translations",
    "primary_category__translations",
    "ampel_score__translations",
    "manufactured_in_country__translations",
    "sub_categories__translations",
    "badges__translations",
    "images__translations",
    "sources__source_type__translations",
    "specs__translations",
    "faqs__translations",
    "pros_cons__translations",
    "pros_cons__type__translations",
    "affiliate_links__availability__translations",
)
# ... and what only one group has.
GROUP_PREFETCH = {
    "toys_learning": ("learning_badges__translations",),
    "school_everyday": (),
    "tech_safety": (
        "data_categories__translations",
        "data_categories__data_type__translations",
        "data_categories__server_region__translations",
    ),
}


def published_product(locale: str, slug: str) -> Product:
    """One published product by its slug in the given language, as the
    class of its group (ToysProduct, SchoolProduct, TechProduct).

    Raises Product.DoesNotExist for unknown slugs and for unpublished
    products alike: a draft must not be distinguishable from a typo. (A
    group's DoesNotExist is a subclass of Product.DoesNotExist.)

    Two queries decide the product: the slug gives its key and main
    category, the group's class then loads it with everything the detail
    serializer touches prefetched, including all translations. The other
    languages are needed for the hreflang alternates, and picking the right
    row out of a prefetched list costs no query.
    """
    pk, main_category = (
        Product.objects.filter(
            is_published=True, translations__locale=locale, translations__slug=slug
        )
        .values_list("pk", "primary_category__slug")
        .get()
    )
    return (
        product_group(main_category)
        .objects.select_related(*DETAIL_SELECT)
        .prefetch_related(*DETAIL_PREFETCH, *GROUP_PREFETCH[main_category])
        .get(pk=pk)
    )


def published_products(locale: str) -> QuerySet[Product]:
    """All published products that have a translation in the given language,
    prefetched for the list serializer. Bare Product rows: a list card shows
    only what every product has."""
    return (
        Product.objects.filter(is_published=True, translations__locale=locale)
        .select_related("primary_category")
        .prefetch_related(
            "translations",
            "badges__translations",
            "primary_category__translations",
        )
        .order_by("-published_at")
    )


def concrete_product(product: Product) -> Product:
    """The product as the class of its group. A Product loaded through
    Product.objects has none of its group's columns, texts or lists."""
    group = product_group(product.primary_category.slug)
    if isinstance(product, group):
        return product
    return group.objects.get(pk=product.pk)


def analysis_choices() -> dict[str, list[str]]:
    """The values the analysis may choose from, per field of its answer.
    Read from the choice lists, so a value added in the admin is offered to
    the next analysis without a code change."""
    return {
        "pros_con_type": list(ProsConType.objects.values_list("slug", flat=True)),
        "data_type": list(DataType.objects.values_list("slug", flat=True)),
        "server_region": list(ServerRegion.objects.values_list("slug", flat=True)),
    }


def product_slug_taken(locale: str, slug: str) -> bool:
    """Used by the pipeline before it writes a slug."""
    return ProductTranslation.objects.filter(locale=locale, slug=slug).exists()
