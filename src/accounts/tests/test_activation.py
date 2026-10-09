import re
from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import User
from accounts.services import send_activation_email
from accounts.tokens import ActivationTokenGenerator, activation_token_generator
from audit.models import AuditEvent

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "a-brand-new-passphrase"


@pytest.fixture
def pending_user(db):
    return User.objects.create_user("bob@example.com", first_name="Bob", last_name="Martin")


def activation_url(user, token=None):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = token or activation_token_generator.make_token(user)
    return reverse("accounts:activate", args=[uid, token])


def post_password(client, url, password=NEW_PASSWORD):
    return client.post(url, {"new_password1": password, "new_password2": password})


def test_token_generator_is_distinct_from_password_reset(pending_user):
    assert activation_token_generator.key_salt == "accounts.tokens.ActivationTokenGenerator"
    from django.contrib.auth.tokens import default_token_generator

    token = activation_token_generator.make_token(pending_user)
    assert not default_token_generator.check_token(pending_user, token)


@pytest.mark.parametrize("token", ["", "nodash", "zz!-abc", "1-abc"])
def test_malformed_tokens_are_rejected(pending_user, token):
    assert not activation_token_generator.check_token(pending_user, token)
    assert not activation_token_generator.check_token(None, "1-abc")


def test_get_shows_set_password_form(client, pending_user):
    response = client.get(activation_url(pending_user))
    assert response.status_code == 200
    assert 'name="new_password1"' in response.content.decode()


def test_activation_sets_password_activates_audits_and_logs_in(client, pending_user):
    response = post_password(client, activation_url(pending_user))
    assert response.status_code == 302
    assert response.url == reverse("home")
    pending_user.refresh_from_db()
    assert pending_user.status == User.Status.ACTIVE
    assert pending_user.is_active
    assert pending_user.activated_at is not None
    assert pending_user.check_password(NEW_PASSWORD)
    assert client.session["_auth_user_id"] == str(pending_user.pk)
    event = AuditEvent.objects.get(action="user.activated")
    assert event.target_id == str(pending_user.public_id)
    assert event.changes == {"status": ["pending", "active"]}


def test_link_valid_within_72_hours(client, pending_user):
    url = activation_url(pending_user)
    later = activation_token_generator._now() + timedelta(hours=71)
    with mock.patch.object(ActivationTokenGenerator, "_now", return_value=later):
        assert client.get(url).status_code == 200


def test_link_rejected_after_72_hours(client, pending_user):
    url = activation_url(pending_user)
    later = activation_token_generator._now() + timedelta(hours=72, seconds=1)
    with mock.patch.object(ActivationTokenGenerator, "_now", return_value=later):
        response = client.get(url)
        assert response.status_code == 400
        assert post_password(client, url).status_code == 400
    pending_user.refresh_from_db()
    assert pending_user.status == User.Status.PENDING


def test_activation_timeout_uses_its_own_setting(client, pending_user, settings):
    settings.PASSWORD_RESET_TIMEOUT = 1
    settings.ACCOUNT_ACTIVATION_TIMEOUT = 72 * 3600
    url = activation_url(pending_user)
    later = activation_token_generator._now() + timedelta(hours=2)
    with mock.patch.object(ActivationTokenGenerator, "_now", return_value=later):
        assert client.get(url).status_code == 200


def test_link_rejected_after_use(client, pending_user):
    url = activation_url(pending_user)
    post_password(client, url)
    client.post(reverse("accounts:logout"))
    response = client.get(url)
    assert response.status_code == 400
    assert "invalid" in response.content.decode().lower()


def test_link_rejected_when_user_is_not_pending(client, pending_user):
    url = activation_url(pending_user)
    pending_user.status = User.Status.SUSPENDED
    pending_user.save()
    assert client.get(url).status_code == 400


def test_bad_uid_is_rejected(client):
    assert client.get(reverse("accounts:activate", args=["zzzz", "1-abc"])).status_code == 400


def test_password_validators_enforced(client, pending_user):
    response = post_password(client, activation_url(pending_user), "elevenchars")
    assert response.status_code == 200
    assert "too short" in response.content.decode()
    pending_user.refresh_from_db()
    assert pending_user.status == User.Status.PENDING


def test_activation_pages_in_french(client, pending_user):
    response = client.get(activation_url(pending_user), HTTP_ACCEPT_LANGUAGE="fr")
    assert b'<html lang="fr"' in response.content
    assert "Activer" in response.content.decode()
    invalid = client.get(
        reverse("accounts:activate", args=["zzzz", "1-abc"]), HTTP_ACCEPT_LANGUAGE="fr"
    )
    assert "invalide" in invalid.content.decode()


def test_send_activation_email(pending_user, settings, django_capture_on_commit_callbacks):
    settings.SITE_URL = "https://communities.example.com/"
    with django_capture_on_commit_callbacks(execute=True):
        send_activation_email(pending_user)
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["bob@example.com"]
    match = re.search(r"https://communities\.example\.com/accounts/activate/\S+/\S+/", message.body)
    assert match
    assert "Activate" in message.subject or "activate" in message.subject


def test_send_activation_email_in_user_language(
    client, pending_user, django_capture_on_commit_callbacks
):
    pending_user.profile.language = "fr"
    pending_user.profile.save()
    with django_capture_on_commit_callbacks(execute=True):
        send_activation_email(pending_user)
    message = mail.outbox[0]
    assert "Communautés" in message.subject
    link = re.search(r"http://localhost:8000(/accounts/activate/\S+/\S+/)", message.body)
    assert client.get(link.group(1)).status_code == 200


def test_activation_does_not_sign_in_when_local_login_is_disabled(client, pending_user, settings):
    settings.AUTH_MODE = "oidc"
    settings.BREAK_GLASS_EMAIL = ""
    response = post_password(client, activation_url(pending_user))
    assert response.status_code == 302
    assert response.url == reverse("accounts:login")
    assert "_auth_user_id" not in client.session
    pending_user.refresh_from_db()
    assert pending_user.status == User.Status.ACTIVE
