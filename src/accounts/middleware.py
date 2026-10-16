from collections.abc import Callable
from urllib.parse import quote

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from rest_framework.views import APIView

from .policies import has_confirmed_device, requires_mfa

# URL names reachable before the second factor is verified.
ALLOWED_URL_NAMES = frozenset(
    {
        "accounts:logout",
        "accounts:mfa_setup",
        "accounts:mfa_verify",
        "set_language",
        "healthz",
        "readyz",
        "metrics",
    }
)


def _is_api_view(request: HttpRequest, view_func) -> bool:
    """A DRF view under ``/api/``: its default permission enforces MFA with a JSON 403.

    A plain Django view mounted under ``/api/`` is not exempted (it keeps the redirect).
    """
    if not request.path_info.startswith("/api/"):
        return False
    view_class = getattr(view_func, "cls", None)
    return isinstance(view_class, type) and issubclass(view_class, APIView)


class MFARequiredMiddleware:
    """Send privileged users to MFA setup or verification until their session is verified.

    Must come after ``django_otp.middleware.OTPMiddleware``, which provides
    ``request.user.is_verified()``.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_view(self, request: HttpRequest, view_func, view_args, view_kwargs):
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False) or user.is_verified():
            return None
        if not requires_mfa(user):
            return None
        if _is_api_view(request, view_func):
            return None
        # Static files are normally served before this middleware; harmless safety net.
        if request.path_info.startswith(settings.STATIC_URL):
            return None
        # The hidden admin must look the same as an unknown path until MFA is done.
        if request.path_info.startswith("/" + settings.DJANGO_ADMIN_PATH):
            raise Http404
        match = request.resolver_match
        if match is not None and match.view_name in ALLOWED_URL_NAMES:
            return None
        name = "accounts:mfa_verify" if has_confirmed_device(user) else "accounts:mfa_setup"
        return HttpResponseRedirect(f"{reverse(name)}?next={quote(request.get_full_path())}")
