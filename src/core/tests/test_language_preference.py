from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse
from django.utils import timezone, translation

from accounts.models import UserProfile
from core.middleware import UserPreferencesMiddleware

pytestmark = pytest.mark.django_db


def set_language(user, language):
    user.profile.language = language
    user.profile.save()


def test_saved_language_wins_over_accept_language(client, active_user):
    set_language(active_user, "fr")
    client.force_login(active_user)
    response = client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="en")
    assert b'<html lang="fr"' in response.content
    assert "Communautés".encode() in response.content
    assert response.cookies["django_language"].value == "fr"


def test_saved_language_wins_over_cookie(client, active_user):
    set_language(active_user, "fr")
    client.force_login(active_user)
    client.cookies["django_language"] = "en"
    response = client.get(reverse("home"))
    assert b'<html lang="fr"' in response.content
    assert response.cookies["django_language"].value == "fr"


def test_cookie_not_rewritten_when_already_matching(client, active_user):
    set_language(active_user, "fr")
    client.force_login(active_user)
    client.cookies["django_language"] = "fr"
    response = client.get(reverse("home"))
    assert "django_language" not in response.cookies


def test_automatic_language_follows_accept_language(client, active_user):
    client.force_login(active_user)
    response = client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="fr")
    assert b'<html lang="fr"' in response.content
    assert "django_language" not in response.cookies


def test_language_kept_after_logout(client, active_user):
    set_language(active_user, "fr")
    client.force_login(active_user)
    client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="en")
    client.post(reverse("accounts:logout"))
    response = client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="en")
    assert b'<html lang="fr"' in response.content


def test_set_language_while_logged_in_persists_preference(client, active_user):
    client.force_login(active_user)
    response = client.post(reverse("set_language"), {"language": "fr", "next": "/"})
    assert response.status_code == 302
    assert UserProfile.objects.get(user=active_user).language == "fr"
    assert response.cookies["django_language"].value == "fr"
    fresh = Client()
    fresh.force_login(active_user)
    assert b'<html lang="fr"' in fresh.get("/", HTTP_ACCEPT_LANGUAGE="en").content


def test_set_language_switch_back_overrides_old_preference(client, active_user):
    set_language(active_user, "fr")
    client.force_login(active_user)
    response = client.post(reverse("set_language"), {"language": "en", "next": "/"})
    assert response.cookies["django_language"].value == "en"
    assert UserProfile.objects.get(user=active_user).language == "en"
    assert b'<html lang="en"' in client.get("/").content


def test_set_language_ignores_unknown_language(client, active_user):
    client.force_login(active_user)
    client.post(reverse("set_language"), {"language": "de", "next": "/"})
    assert UserProfile.objects.get(user=active_user).language == ""


def test_set_language_anonymous_still_works(client):
    response = client.post(reverse("set_language"), {"language": "fr", "next": "/"})
    assert response.status_code == 302
    assert response.cookies["django_language"].value == "fr"


def test_middleware_activates_timezone_only_during_request(rf, active_user):
    active_user.profile.timezone = "Asia/Tokyo"
    active_user.profile.save()
    captured = {}

    def view(request):
        captured["tz"] = timezone.get_current_timezone()
        captured["lang"] = translation.get_language()
        return HttpResponse()

    request = rf.get("/")
    request.user = active_user
    UserPreferencesMiddleware(view)(request)
    assert captured["tz"] == ZoneInfo("Asia/Tokyo")
    assert timezone.get_current_timezone_name() == "Europe/Paris"

    request = rf.get("/")
    request.user = AnonymousUser()
    UserPreferencesMiddleware(view)(request)
    assert str(captured["tz"]) == "Europe/Paris"


def test_middleware_ignores_unsupported_saved_language(rf, active_user):
    UserProfile.objects.filter(user=active_user).update(language="de")
    active_user.refresh_from_db()
    request = rf.get("/")
    request.user = active_user
    with translation.override("en"):
        response = UserPreferencesMiddleware(lambda r: HttpResponse())(request)
        assert translation.get_language() == "en"
    assert "django_language" not in response.cookies
