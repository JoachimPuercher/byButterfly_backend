"""URLs of the jenymia API, version 1.

Mounted in core/urls.py under /api/v1/jenymia/. The version belongs to the
contract: inside v1 fields may be added, never renamed or removed. A shape
that cannot be kept compatible becomes v2 next to v1, with the domain
untouched.
"""

from django.urls import path

from .views import ProductDetailView, ProductListView

app_name = "jenymia_v1"

urlpatterns = [
    path(
        "products/<slug:locale>/",
        ProductListView.as_view(),
        name="product-list",
    ),
    path(
        "products/<slug:locale>/<slug:slug>/",
        ProductDetailView.as_view(),
        name="product-detail",
    ),
]
