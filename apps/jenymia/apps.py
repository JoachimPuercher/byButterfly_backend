from django.apps import AppConfig


class JenymiaConfig(AppConfig):
    name = "apps.jenymia"

    def ready(self) -> None:
        # Registers the system checks.
        from . import checks  # noqa: F401
