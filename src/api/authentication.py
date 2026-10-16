"""Session authentication for the REST API (spec § 15: same origin, no other scheme)."""

from rest_framework import authentication


class SessionAuthentication(authentication.SessionAuthentication):
    """DRF session authentication that answers 401 (not 403) to anonymous callers.

    DRF turns ``NotAuthenticated`` into a 403 when the first authentication class has no
    ``authenticate_header``. This scheme name only lets it answer 401. The API never accepts
    credentials: logging in stays in the UI (axes, MFA, SSO modes).
    """

    def authenticate_header(self, request):
        return 'Session realm="api"'
