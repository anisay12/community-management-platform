"""OpenAPI description of the API's session authentication (drf-spectacular)."""

from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class SessionAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "api.authentication.SessionAuthentication"
    name = "sessionAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "cookie", "name": settings.SESSION_COOKIE_NAME}
