"""Rate limits for the public jenymia endpoints.

Each class carries its own scope; the matching rate is set in
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] in the settings.

The counter lives in the Django cache, which is in-memory for now. That is
enough while no frontend calls the API, but it counts per process: before
the frontend goes live the cache has to become a shared backend, or the
limit is a guideline rather than a limit.
"""

from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class ProductDetailAnonThrottle(AnonRateThrottle):
    """Anonymous visitors, counted per IP address."""

    scope = "jenymia_product_detail_anon"


class ProductDetailUserThrottle(UserRateThrottle):
    """Logged-in users, counted per user id. No login exists yet, so this
    only applies to staff sessions - it is in place so the endpoint does not
    need touching when login arrives."""

    scope = "jenymia_product_detail_user"
