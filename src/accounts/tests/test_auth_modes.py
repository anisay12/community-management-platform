"""AUTH_MODE: local, mixed and sso_only sign-in, break-glass account, password lockdown."""

import logging
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user
from django.contrib.messages import get_messages
from django.core import mail
from django.core.management import CommandError, call_command
from django.urls import reverse

from accounts.models import User
from audit.models import AuditEvent
from conftest import PASSWORD

pytestmark = pytest.mark.django_db

LOGIN_URL = reverse("accounts:login")
INVALID = "Invalid email or password."
SSO_BUTTON = "Sign in with your Talan account"
EMERGENCY = "Emergency access"
BREAK_GLASS = "rescue@example.com"


def post_login(client, email, password=PASSWORD):
    return client.post(
        LOGIN_URL, {"username": email, "password": password}, REMOTE_ADDR="198.51.100.1"
    )


@pytest.fixture
def break_glass(settings, make_user):
    settings.BREAK_GLASS_EMAIL = "Rescue@Example.com"
    settings.ADMINS = [("Ops", "ops@example.com")]
    return make_user(BREAK_GLASS, first_name="Rescue", last_name="Account")


# local mode --------------------------------------------------------------------------


def test_local_mode_has_no_oidc_urls(client):
    assert client.get("/oidc/authenticate/").status_code == 404
    assert client.get("/oidc/callback/").status_code == 404


def test_local_mode_login_page_has_only_the_password_form(client):
    content = client.get(LOGIN_URL).content.decode()
    assert 'name="password"' in content
    assert SSO_BUTTON not in content
    assert EMERGENCY not in content


def test_oidc_backend_is_not_enabled_in_local_mode(settings):
    assert "accounts.oidc.TalanOIDCBackend" not in settings.AUTHENTICATION_BACKENDS
    assert "mozilla_django_oidc" in settings.INSTALLED_APPS


# mixed mode --------------------------------------------------------------------------


def test_mixed_mode_offers_sso_and_password(client, use_auth_mode):
    use_auth_mode("mixed")
    content = client.get(LOGIN_URL, {"next": "/me/"}).content.decode()
    assert SSO_BUTTON in content
    assert reverse("oidc_authentication_init") + "?next=%2Fme%2F" in content
    assert 'name="password"' in content
    assert EMERGENCY not in content


def test_sso_initiation_redirects_to_the_identity_provider(client, use_auth_mode):
    use_auth_mode("mixed")
    response = client.get(reverse("oidc_authentication_init"))
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert location.netloc == "idp.example.com"
    params = parse_qs(location.query)
    assert params["scope"] == ["openid email profile"]
    assert params["code_challenge_method"] == ["S256"]


def test_mixed_mode_password_login_still_works(client, use_auth_mode, active_user):
    use_auth_mode("mixed")
    assert post_login(client, "alice@example.com").status_code == 302
    assert get_user(client).is_authenticated


def test_failed_sso_callback_returns_to_login_with_a_message(client, use_auth_mode):
    use_auth_mode("mixed")
    response = client.get(reverse("oidc_authentication_callback"), {"error": "access_denied"})
    assert response.status_code == 302
    assert response["Location"] == LOGIN_URL
    assert [str(m) for m in get_messages(response.wsgi_request)] == [
        "Single sign-on did not complete. If your account is new or inactive, "
        "contact an administrator."
    ]


# sso_only mode -----------------------------------------------------------------------


def test_sso_only_hides_the_password_form(client, use_auth_mode):
    use_auth_mode("sso_only")
    content = client.get(LOGIN_URL).content.decode()
    assert SSO_BUTTON in content
    assert 'name="password"' not in content
    assert EMERGENCY in content
    assert reverse("accounts:password_reset") not in content


def test_sso_only_emergency_link_shows_the_password_form(client, use_auth_mode):
    use_auth_mode("sso_only")
    content = client.get(LOGIN_URL, {"local": "1"}).content.decode()
    assert 'name="password"' in content


def test_sso_only_refuses_password_login_for_normal_users(client, use_auth_mode, active_user):
    use_auth_mode("sso_only")
    response = post_login(client, "alice@example.com")
    assert response.status_code == 200
    assert INVALID in response.content.decode()
    assert not get_user(client).is_authenticated
    assert not AuditEvent.objects.filter(action="auth.break_glass_login").exists()
    assert mail.outbox == []


def test_sso_only_allows_break_glass_with_audit_and_alert(
    client, use_auth_mode, break_glass, caplog
):
    use_auth_mode("sso_only")
    with caplog.at_level(logging.WARNING, logger="accounts.backends"):
        response = post_login(client, "RESCUE@example.com")
    assert response.status_code == 302
    assert get_user(client) == break_glass
    event = AuditEvent.objects.get(action="auth.break_glass_login")
    assert event.actor == break_glass
    assert event.target_id == str(break_glass.public_id)
    assert any("break_glass_login" in str(record.msg) for record in caplog.records)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["ops@example.com"]
    assert BREAK_GLASS in mail.outbox[0].body


def test_break_glass_wrong_password_sends_no_alert(client, use_auth_mode, break_glass):
    use_auth_mode("sso_only")
    assert post_login(client, BREAK_GLASS, "wrong-password").status_code == 200
    assert mail.outbox == []
    assert not AuditEvent.objects.filter(action="auth.break_glass_login").exists()


# disable_local_passwords -------------------------------------------------------------


def test_disable_local_passwords_requires_sso_only(settings, active_user):
    settings.AUTH_MODE = "mixed"
    with pytest.raises(CommandError, match="sso_only"):
        call_command("disable_local_passwords")
    active_user.refresh_from_db()
    assert active_user.has_usable_password()


def test_disable_local_passwords_dry_run_changes_nothing(
    settings, break_glass, active_user, capsys
):
    settings.AUTH_MODE = "sso_only"
    call_command("disable_local_passwords", "--dry-run")
    active_user.refresh_from_db()
    assert active_user.has_usable_password()
    assert "1" in capsys.readouterr().out
    assert not AuditEvent.objects.filter(action="auth.local_passwords_disabled").exists()


def test_disable_local_passwords_keeps_break_glass(settings, break_glass, make_user, capsys):
    settings.AUTH_MODE = "sso_only"
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    make_user("sso@example.com", password=None)
    call_command("disable_local_passwords")
    for user in (alice, bob):
        user.refresh_from_db()
        assert not user.has_usable_password()
    break_glass.refresh_from_db()
    assert break_glass.check_password(PASSWORD)
    out = capsys.readouterr().out
    assert "Disabled local passwords for 2 account(s)" in out
    event = AuditEvent.objects.get(action="auth.local_passwords_disabled")
    assert event.changes == {"count": 2, "break_glass_kept": True}
    assert User.objects.filter(password__startswith="!").count() == 3
