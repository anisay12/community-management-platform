from base64 import b32encode
from urllib.parse import quote

import pytest
from django.contrib.auth.models import AnonymousUser, Group
from django.core.cache import cache
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.policies import requires_mfa
from accounts.roles import Role
from audit.models import AuditEvent

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def admin_user(make_user):
    user = make_user("admin@example.com")
    user.groups.add(Group.objects.get(name=Role.FUNCTIONAL_ADMIN))
    return user


def code_for(device) -> str:
    return f"{totp(device.bin_key, step=device.step, digits=device.digits):06d}"


def wrong_code(device) -> str:
    return f"{(int(code_for(device)) + 1) % 1_000_000:06d}"


def confirmed_device(user) -> TOTPDevice:
    return TOTPDevice.objects.create(user=user, name="default", confirmed=True)


# Policy -------------------------------------------------------------------------


def test_requires_mfa_for_privileged_staff_and_break_glass(make_user, settings):
    settings.BREAK_GLASS_EMAIL = "Rescue@Example.com"
    employee = make_user("emp@example.com")
    employee.groups.add(Group.objects.get(name=Role.EMPLOYEE))
    auditor = make_user("audit@example.com")
    auditor.groups.add(Group.objects.get(name=Role.AUDITOR))
    tech = make_user("tech@example.com")
    tech.groups.add(Group.objects.get(name=Role.TECHNICAL_ADMIN))
    staff = make_user("staff@example.com", is_staff=True)
    superuser = make_user("root@example.com", is_superuser=True)
    rescue = make_user("rescue@example.com")

    assert not requires_mfa(AnonymousUser())
    assert not requires_mfa(employee)
    assert not requires_mfa(auditor)
    assert requires_mfa(tech)
    assert requires_mfa(staff)
    assert requires_mfa(superuser)
    assert requires_mfa(rescue)


def test_break_glass_unset_does_not_match_anyone(make_user, settings):
    settings.BREAK_GLASS_EMAIL = ""
    assert not requires_mfa(make_user("someone@example.com"))


# Middleware ---------------------------------------------------------------------


def test_employee_is_never_asked_for_mfa(client, make_user):
    employee = make_user("emp@example.com")
    employee.groups.add(Group.objects.get(name=Role.EMPLOYEE))
    client.force_login(employee)
    assert client.get(reverse("home")).status_code == 200


def test_privileged_user_without_device_is_sent_to_setup_with_next(client, admin_user):
    client.force_login(admin_user)
    response = client.get("/?tab=1")
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:mfa_setup") + "?next=" + quote("/?tab=1")


def test_password_login_of_privileged_user_leads_to_mfa_setup(client, admin_user):
    response = client.post(
        reverse("accounts:login"), {"username": admin_user.email, "password": PASSWORD}
    )
    assert response.status_code == 302
    response = client.get(response["Location"])
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:mfa_setup"))


def test_privileged_user_with_device_is_sent_to_verify(client, admin_user):
    confirmed_device(admin_user)
    client.force_login(admin_user)
    response = client.get(reverse("home"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:mfa_verify") + "?next=")


@pytest.mark.parametrize(
    "path",
    ["/healthz", "/readyz", "/static/core/app.css"],
)
def test_allow_listed_paths_are_not_redirected(client, admin_user, path):
    client.force_login(admin_user)
    response = client.get(path)
    assert not (response.status_code == 302 and "/accounts/mfa/" in response.get("Location", ""))


def test_unverified_privileged_user_can_log_out_and_switch_language(client, admin_user):
    client.force_login(admin_user)
    response = client.post(reverse("set_language"), {"language": "fr", "next": "/"})
    assert response.status_code == 302
    assert response["Location"] == "/"
    response = client.post(reverse("accounts:logout"))
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:login")


# Setup --------------------------------------------------------------------------


def test_setup_shows_qr_code_and_reuses_unconfirmed_device(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse("accounts:mfa_setup"))
    assert response.status_code == 200
    device = TOTPDevice.objects.get(user=admin_user)
    assert not device.confirmed
    content = response.content.decode()
    assert "<svg" in content
    assert b32encode(device.bin_key).decode() in content
    assert "Cache-Control" in response and "no-store" in response["Cache-Control"]

    client.get(reverse("accounts:mfa_setup"))
    assert TOTPDevice.objects.filter(user=admin_user).count() == 1
    assert TOTPDevice.objects.get(user=admin_user).key == device.key


def test_setup_with_valid_code_confirms_device_and_grants_access(client, admin_user):
    client.force_login(admin_user)
    client.get(reverse("accounts:mfa_setup"))
    device = TOTPDevice.objects.get(user=admin_user)

    response = client.post(
        reverse("accounts:mfa_setup") + "?next=/", {"otp_token": code_for(device)}
    )

    assert response.status_code == 302
    assert response["Location"] == "/"
    device.refresh_from_db()
    assert device.confirmed
    assert client.get(reverse("home")).status_code == 200
    event = AuditEvent.objects.get(action="auth.mfa_enrolled")
    assert event.actor == admin_user
    assert event.target_id == str(admin_user.public_id)


def test_setup_rejects_wrong_code(client, admin_user):
    client.force_login(admin_user)
    client.get(reverse("accounts:mfa_setup"))
    device = TOTPDevice.objects.get(user=admin_user)

    response = client.post(reverse("accounts:mfa_setup"), {"otp_token": wrong_code(device)})

    assert response.status_code == 200
    assert "Invalid code" in response.content.decode()
    device.refresh_from_db()
    assert not device.confirmed
    assert client.get(reverse("home")).status_code == 302
    assert not AuditEvent.objects.filter(action="auth.mfa_enrolled").exists()


def test_setup_redirects_to_verify_when_device_already_confirmed(client, admin_user):
    confirmed_device(admin_user)
    client.force_login(admin_user)
    response = client.get(reverse("accounts:mfa_setup"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:mfa_verify"))
    assert TOTPDevice.objects.filter(user=admin_user).count() == 1


def test_mfa_pages_require_login(client):
    for name in ("accounts:mfa_setup", "accounts:mfa_verify"):
        response = client.get(reverse(name))
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("accounts:login"))


# Verify -------------------------------------------------------------------------


def test_verify_with_valid_code_grants_access_and_is_audited(client, admin_user):
    device = confirmed_device(admin_user)
    client.force_login(admin_user)
    assert client.get(reverse("accounts:mfa_verify")).status_code == 200

    response = client.post(
        reverse("accounts:mfa_verify") + "?next=/%3Fa%3D1", {"otp_token": code_for(device)}
    )

    assert response.status_code == 302
    assert response["Location"] == "/?a=1"
    assert client.get(reverse("home")).status_code == 200
    event = AuditEvent.objects.get(action="auth.mfa_verified")
    assert event.actor == admin_user


def test_verify_ignores_unsafe_next(client, admin_user):
    device = confirmed_device(admin_user)
    client.force_login(admin_user)
    response = client.post(
        reverse("accounts:mfa_verify") + "?next=https://evil.example.com/",
        {"otp_token": code_for(device)},
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("home")


def test_verify_rejects_wrong_code_with_neutral_error(client, admin_user):
    device = confirmed_device(admin_user)
    client.force_login(admin_user)
    response = client.post(reverse("accounts:mfa_verify"), {"otp_token": wrong_code(device)})
    assert response.status_code == 200
    assert "Invalid code" in response.content.decode()
    assert client.get(reverse("home")).status_code == 302
    assert not AuditEvent.objects.filter(action="auth.mfa_verified").exists()


def test_verify_without_device_redirects_to_setup(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse("accounts:mfa_verify"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:mfa_setup"))


def test_sixth_wrong_code_in_window_is_rate_limited(client, admin_user):
    device = confirmed_device(admin_user)
    client.force_login(admin_user)
    url = reverse("accounts:mfa_verify")
    for _ in range(5):
        assert client.post(url, {"otp_token": wrong_code(device)}).status_code == 200

    response = client.post(url, {"otp_token": wrong_code(device)})
    assert response.status_code == 429
    # Even the right code is refused until the window expires.
    assert client.post(url, {"otp_token": code_for(device)}).status_code == 429
    assert client.get(reverse("home")).status_code == 302


def test_verified_user_is_sent_on_from_mfa_pages(client, admin_user):
    device = confirmed_device(admin_user)
    client.force_login(admin_user)
    client.post(reverse("accounts:mfa_verify"), {"otp_token": code_for(device)})
    for name in ("accounts:mfa_setup", "accounts:mfa_verify"):
        response = client.get(reverse(name) + "?next=/")
        assert response.status_code == 302
        assert response["Location"] == "/"


def test_setup_failures_are_rate_limited_too(client, admin_user):
    client.force_login(admin_user)
    client.get(reverse("accounts:mfa_setup"))
    url = reverse("accounts:mfa_setup")
    for _ in range(5):
        assert client.post(url, {"otp_token": "abc"}).status_code == 200
    assert client.post(url, {"otp_token": "000000"}).status_code == 429
