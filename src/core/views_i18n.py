"""Language switching that also remembers the choice on the user's profile."""

from django.views.i18n import set_language as django_set_language

from .i18n import supported_languages


def set_language(request):
    """Django's ``set_language``, plus saving the choice for a logged-in user."""
    response = django_set_language(request)
    language = request.POST.get("language", "")
    user = request.user
    if request.method == "POST" and user.is_authenticated and language in supported_languages():
        profile = getattr(user, "profile", None)
        if profile is not None and profile.language != language:
            profile.language = language
            profile.save(update_fields=["language", "updated_at"])
    return response
