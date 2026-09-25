"""Read access to jenymia data.

Views never touch Model.objects directly. The rule "only published products
leave the backend" is written down once, here - not in every view, where it
is forgotten exactly once and then drafts are public.
"""

from .models import Product, ProductTranslation


def published_product(locale: str, slug: str) -> Product:
    """One published product by its slug in the given language.

    Raises Product.DoesNotExist for unknown slugs and for unpublished
    products alike: a draft must not be distinguishable from a typo.

    Everything the detail serializer touches is prefetched, including all
    translations. The other languages are needed for the hreflang
    alternates, and picking the right row out of a prefetched list costs no
    query.
    """
    return (
        Product.objects.filter(is_published=True)
        .select_related("brand", "author", "primary_category")
        .prefetch_related(
            "translations",
            "primary_category__translations",
            "categories__translations",
            "badges__translations",
            "learning_badges__translations",
            "contexts__translations",
            "images__translations",
            "sources",
            "specs__translations",
            "faqs__translations",
            "pros_cons__translations",
            "affiliate_links",
            "data_categories__translations",
        )
        .get(translations__locale=locale, translations__slug=slug)
    )


def product_slug_taken(locale: str, slug: str) -> bool:
    """Used by the pipeline before it writes a slug."""
    return ProductTranslation.objects.filter(locale=locale, slug=slug).exists()
