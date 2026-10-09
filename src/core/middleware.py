import re
import uuid
import zoneinfo
from collections.abc import Callable
from datetime import timedelta

import structlog
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils import timezone, translation

from .context import bind_request, clear_request, set_user_source
from .views_i18n import SUPPORTED_LANGUAGES, set_language_cookie

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class RequestIDMiddleware:
    """Attach a correlation identifier to each request, to the logs and to the response."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        bind_request(request)
        try:
            response = self.get_response(request)
        finally:
            structlog.contextvars.clear_contextvars()
            clear_request()
        response[REQUEST_ID_HEADER] = request_id
        return response


class RequestUserContextMiddleware:
    """Expose the authenticated user to code that has no request (signals, services)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        set_user_source(lambda: getattr(request, "user", None))
        return self.get_response(request)


ACTIVITY_INTERVAL = timedelta(minutes=5)


class ActivityMiddleware:
    """Track the user's last activity.

    ``last_seen_at`` is written at most once per ``ACTIVITY_INTERVAL``; the session is
    re-saved at the same moment, which slides its inactivity expiry.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            now = timezone.now()
            if user.last_seen_at is None or now - user.last_seen_at >= ACTIVITY_INTERVAL:
                user._meta.model._default_manager.filter(pk=user.pk).update(last_seen_at=now)
                user.last_seen_at = now
                request.session.modified = True
                self._track_session(request, user)
        return self.get_response(request)

    @staticmethod
    def _track_session(request: HttpRequest, user) -> None:
        # The session key may have been rotated (cycle_key) since login: make sure the
        # current key is tracked so that end_all_sessions can reach it.
        key = request.session.session_key
        if key:
            from accounts.models import UserSession

            UserSession.objects.get_or_create(session_key=key, defaults={"user": user})


class UserPreferencesMiddleware:
    """Apply the logged-in user's saved language and time zone to the request.

    Must come after ``AuthenticationMiddleware`` and ``LocaleMiddleware``: a saved
    language overrides the cookie and ``Accept-Language`` negotiation. The language
    cookie is aligned with the saved preference so that it survives logout.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        profile = self._profile(request)
        if profile is not None:
            language = self._language(profile)
            if language:
                translation.activate(language)
                request.LANGUAGE_CODE = language
            self._activate_timezone(profile)
        else:
            timezone.deactivate()
        try:
            response = self.get_response(request)
        finally:
            timezone.deactivate()
        if profile is not None:
            # Re-read: the view may just have changed the preference (same instance).
            language = self._language(profile)
            if (
                language
                and settings.LANGUAGE_COOKIE_NAME not in response.cookies
                and request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME) != language
            ):
                set_language_cookie(response, language)
        return response

    @staticmethod
    def _profile(request: HttpRequest):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        return getattr(user, "profile", None)

    @staticmethod
    def _language(profile) -> str:
        language = profile.language
        return language if language in SUPPORTED_LANGUAGES else ""

    @staticmethod
    def _activate_timezone(profile) -> None:
        try:
            timezone.activate(zoneinfo.ZoneInfo(profile.timezone))
        except (ValueError, zoneinfo.ZoneInfoNotFoundError):
            timezone.deactivate()
