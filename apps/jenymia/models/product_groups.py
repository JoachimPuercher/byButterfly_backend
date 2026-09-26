"""The product groups: what only the products of one main category have.

One class per main category, each inheriting from Product (multi-table
inheritance): the shared columns stay on jenymia_product, the group's own
columns sit in the group's table, joined by the primary key. Everything
that points at Product - translations, specs, FAQs, badges, the unique URL
- therefore works for every group alike, while a tech product carries no
safety texts and a toy no data categories.

Article-specific facts (the volume of a drinking bottle, the battery life
of a watch) are not a group of their own: they are specs.
"""

from django.db import models

from .base import TranslationBase
from .lookups import LearningBadge
from .product import Product


class ChildCertifiedFields(models.Model):
    """Columns of every group whose products children use themselves."""

    is_child_certified = models.BooleanField(default=False)

    class Meta:
        abstract = True


class SafetyTexts(models.Model):
    """The safety texts of the groups whose products children handle."""

    safety_short = models.TextField(blank=True)
    safety_long = models.TextField(blank=True)

    class Meta:
        abstract = True


class ToysProduct(ChildCertifiedFields, Product):
    MAIN_CATEGORY_SLUG = "toys_learning"

    # Areas of development the toy supports - a toys-only list.
    learning_badges = models.ManyToManyField(
        LearningBadge, related_name="products", blank=True
    )

    class Meta:
        verbose_name = "toys product"


class SchoolProduct(ChildCertifiedFields, Product):
    MAIN_CATEGORY_SLUG = "school_everyday"

    class Meta:
        verbose_name = "school product"


class TechProduct(Product):
    """Devices and apps. Their data categories (details.DataCategory) point
    here, not at Product."""

    MAIN_CATEGORY_SLUG = "tech_safety"

    is_offline_capable = models.BooleanField(default=False)
    requires_account = models.BooleanField(default=False)

    class Meta:
        verbose_name = "tech product"


# The group translations are reached as product.group_translations: the name
# "translations" belongs to ProductTranslation and is inherited by every group.


class ToysProductTranslation(SafetyTexts, TranslationBase):
    product = models.ForeignKey(
        ToysProduct, on_delete=models.CASCADE, related_name="group_translations"
    )
    # How long the toy grows with the child.
    growth_info = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "locale"], name="toys_product_one_text_per_locale"
            )
        ]


class SchoolProductTranslation(SafetyTexts, TranslationBase):
    product = models.ForeignKey(
        SchoolProduct, on_delete=models.CASCADE, related_name="group_translations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "locale"], name="school_product_one_text_per_locale"
            )
        ]


class TechProductTranslation(TranslationBase):
    product = models.ForeignKey(
        TechProduct, on_delete=models.CASCADE, related_name="group_translations"
    )
    privacy_short = models.TextField(blank=True)
    privacy_long = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "locale"], name="tech_product_one_text_per_locale"
            )
        ]


# The group of each main category, by its slug. Services, selectors, the API
# and the admin all pick the class of a product from here.
PRODUCT_GROUPS: dict[str, type[Product]] = {
    group.MAIN_CATEGORY_SLUG: group
    for group in (ToysProduct, SchoolProduct, TechProduct)
}


def product_group(main_category_slug: str) -> type[Product]:
    """The product class of a main category. An unknown slug raises: a main
    category without a group would have no place for its products."""
    try:
        return PRODUCT_GROUPS[main_category_slug]
    except KeyError:
        raise ValueError(
            f"No product group for main category {main_category_slug!r}."
        ) from None
