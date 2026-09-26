"""Public read endpoints for jenymia.

Thin on purpose: the view resolves the language, asks the selector for the
data and hands it to the serializer. It never queries a model itself, so the
"published only" rule cannot be forgotten here.
"""

from django.db.models import QuerySet
from rest_framework import generics, mixins
from rest_framework.exceptions import NotFound
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.jenymia.models import Locale, Product
from apps.jenymia.selectors import published_product, published_products

from .serializers import ProductDetailSerializer, ProductListSerializer
from .throttling import ProductDetailAnonThrottle, ProductDetailUserThrottle


class ProductListView(mixins.ListModelMixin, generics.GenericAPIView):
    """GET /api/v1/jenymia/products/<locale>/

    Simple test list of all published products in one language.
    """

    serializer_class = ProductListSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "jenymia_product_list"

    def get_serializer_context(self) -> dict:
        return {**super().get_serializer_context(), "locale": self.kwargs["locale"]}

    def get_queryset(self) -> QuerySet[Product]:
        locale = self.kwargs["locale"]
        if locale not in Locale.values:
            raise NotFound(f"Unknown locale '{locale}'.")
        return published_products(locale)

    def get(self, request: Request, *args, **kwargs) -> Response:
        return self.list(request, *args, **kwargs)


class ProductDetailView(RetrieveAPIView):
    """GET /api/v1/jenymia/products/<locale>/<slug>/

    The language is part of the path, like on the website. An unknown
    language is a 404 instead of a silent fallback to German - a wrong
    language must not look like a working page.
    """

    serializer_class = ProductDetailSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ProductDetailAnonThrottle, ProductDetailUserThrottle]

    def get_serializer_context(self) -> dict:
        return {**super().get_serializer_context(), "locale": self.kwargs["locale"]}

    def get_object(self) -> Product:
        locale = self.kwargs["locale"]
        if locale not in Locale.values:
            raise NotFound(f"Unknown locale '{locale}'.")
        try:
            return published_product(locale, self.kwargs["slug"])
        except Product.DoesNotExist:
            # Drafts answer exactly like typos: an unpublished product must
            # not be discoverable by probing slugs.
            raise NotFound("Product not found.") from None
