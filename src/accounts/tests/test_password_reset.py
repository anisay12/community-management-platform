import re
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import User
from accounts.tokens import password_reset_token_generator
from audit.models import AuditEvent

pytestmark = pytest.mark.django_db

RESET_URL = reverse("accounts:password_reset")
NEW_PASSWORD = "a-brand-new-passphrase"


def request_reset(client, email, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        return client.post(RESET_URL, {"email": email})


def reset_link(message):
    match = re.search(r"https?://\S+/accounts/reset/\S+/\S+/", message.body)
    assert match, message.body
    return match.group(0)


def test_reset_page_renders_in_both_languages(client):
    assert client.get(RESET_URL).status_code == 200
    french = client.get(RESET_URL, HTTP_ACCEPT_LANGUAGE="fr").content.decode()
    assert "Réinitialiser" in french or "réinitialiser" in french


def test_same_response_for_existing_and_unknown_email(
    client, active_user, django_capture_on_commit_callbacks
):
    known = request_reset(client, "ALICE@example.com", django_capture_on_commit_callbacks)
    unknown = request_reset(client, "ghost@example.com", django_capture_on_commit_callbacks)
    assert known.status_code == unknown.status_code == 302
    assert known.url == unknown.url == reverse("accounts:password_reset_done")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["alice@example.com"]


@pytest.mark.parametrize("status", ["pending", "suspended", "deactivated"])
def test_no_email_for_non_active_user(
    client, make_user, status, django_capture_on_commit_callbacks
):
    make_user(status=status)
    response = request_reset(client, "alice@example.com", django_capture_on_commit_callbacks)
    assert response.status_code == 302
    assert mail.outbox == []


def test_reset_email_uses_site_url_and_user_language(
    client, active_user, settings, django_capture_on_commit_callbacks
):
    settings.SITE_URL = "https://communities.example.com"
    active_user.profile.language = "fr"
    active_user.profile.save()
    request_reset(client, "alice@example.com", django_capture_on_commit_callbacks)
    message = mail.outbox[0]
    assert reset_link(message).startswith("https://communities.example.com/accounts/reset/")
    assert "mot de passe" in message.body.lower()
    assert "\n" not in message.subject


def test_full_reset_flow(client, active_user, django_capture_on_commit_callbacks):
    request_reset(client, "alice@example.com", django_capture_on_commit_callbacks)
    link = reset_link(mail.outbox[0]).split("://", 1)[1].split("/", 1)[1]
    response = client.get("/" + link, follow=True)
    assert response.status_code == 200
    form_url = response.redirect_chain[-1][0]
    response = client.post(form_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD})
    assert response.status_code == 302
    assert response.url == reverse("accounts:password_reset_complete")
    active_user.refresh_from_db()
    assert active_user.check_password(NEW_PASSWORD)
    assert AuditEvent.objects.filter(
        action="auth.password_reset", target_id=str(active_user.public_id)
    ).exists()
    assert client.get(reverse("accounts:password_reset_complete")).status_code == 200


def test_reset_token_expires_after_one_hour(client, active_user):
    token = password_reset_token_generator.make_token(active_user)
    uid = urlsafe_base64_encode(force_bytes(active_user.pk))
    url = reverse("accounts:password_reset_confirm", args=[uid, token])
    issued = password_reset_token_generator._now()
    later = issued + timedelta(seconds=3601)
    with mock.patch.object(PasswordResetTokenGenerator, "_now", return_value=later):
        response = client.get(url, follow=True)
    assert response.status_code == 200
    assert response.context["validlink"] is False


def test_reset_token_invalid_once_user_is_suspended(client, active_user):
    token = password_reset_token_generator.make_token(active_user)
    uid = urlsafe_base64_encode(force_bytes(active_user.pk))
    url = reverse("accounts:password_reset_confirm", args=[uid, token])
    assert client.get(url, follow=True).context["validlink"] is True
    User.objects.filter(pk=active_user.pk).update(status=User.Status.SUSPENDED)
    response = Client().get(url, follow=True)
    assert response.context["validlink"] is False


def test_reset_pages_render_in_french(client):
    for name in ("accounts:password_reset_done", "accounts:password_reset_complete"):
        response = client.get(reverse(name), HTTP_ACCEPT_LANGUAGE="fr")
        assert response.status_code == 200
        assert b'<html lang="fr"' in response.content
    uid = urlsafe_base64_encode(force_bytes(0))
    response = client.get(
        reverse("accounts:password_reset_confirm", args=[uid, "bad-token"]),
        HTTP_ACCEPT_LANGUAGE="fr",
    )
    assert "invalide" in response.content.decode()


def test_reset_rejects_weak_password(client, active_user, django_capture_on_commit_callbacks):
    request_reset(client, "alice@example.com", django_capture_on_commit_callbacks)
    link = reset_link(mail.outbox[0]).split("://", 1)[1].split("/", 1)[1]
    form_url = client.get("/" + link, follow=True).redirect_chain[-1][0]
    response = client.post(form_url, {"new_password1": "short1pass", "new_password2": "short1pass"})
    assert response.status_code == 200
    assert not User.objects.get(pk=active_user.pk).check_password("short1pass")
