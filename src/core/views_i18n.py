"""Language switching that also remembers the choice on the user's profile."""

from django.conf import settings
from django.views.i18n import set_language as django_set_language

SUPPORTED_LANGUAGES = frozenset(code for code, _name in settings.LANGUAGES)


def set_language_cookie(response, language: str) -> None:
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


def set_language(request):
    """Django's ``set_language``, plus saving the choice for a logged-in user."""
    response = django_set_language(request)
    language = request.POST.get("language", "")
    user = request.user
    if request.method == "POST" and user.is_authenticated and language in SUPPORTED_LANGUAGES:
        profile = user.profile
        if profile.language != language:
            profile.language = language
            profile.save(update_fields=["language", "updated_at"])
    return response
