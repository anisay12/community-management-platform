"""Helpers shared by the language middleware and views."""

from django.conf import settings
from django.http import HttpResponse


def supported_languages() -> frozenset[str]:
    """Language codes offered by the site, read from settings at call time."""
    return frozenset(code for code, _name in settings.LANGUAGES)


def set_language_cookie(response: HttpResponse, language: str) -> None:
    """Set the language cookie exactly as Django's ``set_language`` view does."""
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        language,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )


def delete_language_cookie(response: HttpResponse) -> None:
    """Remove the language cookie so negotiation falls back to ``Accept-Language``."""
    response.delete_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )
