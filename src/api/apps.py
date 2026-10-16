from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ApiConfig(AppConfig):
    name = "api"
    verbose_name = _("API")

    def ready(self):
        # Registers the OpenAPI authentication extension.
        from . import schema  # noqa: F401
