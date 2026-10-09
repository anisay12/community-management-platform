from unittest import mock

import pytest
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import Argon2PasswordHasher
from django.test import RequestFactory
from django.urls import reverse

from accounts.backends import EmailBackend, axes_username
from accounts.models import User
from audit.models import AuditEvent

from .conftest import PASSWORD

pytestmark = pytest.mark.django_db

LOGIN_URL = reverse("accounts:login")
INVALID = "Invalid email or password."


def post_login(client, email, password=PASSWORD, ip="198.51.100.1", **extra):
    return client.post(
        LOGIN_URL, {"username": email, "password": password}, REMOTE_ADDR=ip, **extra
    )


def test_settings_interfaces():
    assert settings.PASSWORD_HASHERS  # test settings keep MD5 for speed
    assert settings.AUTHENTICATION_BACKENDS == [
        "axes.backends.AxesStandaloneBackend",
        "accounts.backends.EmailBackend",
    ]
    assert settings.LOGIN_URL == "accounts:login"
    assert settings.LOGIN_REDIRECT_URL == "home"
    assert settings.LOGOUT_REDIRECT_URL == "accounts:login"
    assert settings.PASSWORD_RESET_TIMEOUT == 3600
    assert settings.ACCOUNT_ACTIVATION_TIMEOUT == 72 * 3600
    assert settings.SESSION_COOKIE_AGE == 8 * 3600
    assert settings.SESSION_EXPIRE_AT_BROWSER_CLOSE is False
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.SESSION_COOKIE_SAMESITE == "Lax"
    assert settings.AXES_FAILURE_LIMIT == 5
    assert settings.AXES_LOCKOUT_PARAMETERS == [["username", "ip_address"]]
    assert settings.MIDDLEWARE[-1] == "axes.middleware.AxesMiddleware"
    validators = [v["NAME"].rsplit(".", 1)[-1] for v in settings.AUTH_PASSWORD_VALIDATORS]
    assert validators == [
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    ]
    assert settings.AUTH_PASSWORD_VALIDATORS[1]["OPTIONS"] == {"min_length": 12}


def test_base_settings_put_argon2_first():
    from config.settings import base

    assert base.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"
    assert Argon2PasswordHasher()._load_library()  # argon2-cffi is installed


def test_login_page_renders(client):
    response = client.get(LOGIN_URL)
    assert response.status_code == 200
    content = response.content.decode()
    assert 'type="email"' in content
    assert 'autocomplete="username"' in content
    assert 'autocomplete="current-password"' in content
    assert reverse("accounts:password_reset") in content


def test_login_page_in_french(client):
    response = client.get(LOGIN_URL, HTTP_ACCEPT_LANGUAGE="fr")
    assert b'<html lang="fr"' in response.content
    assert "Mot de passe" in response.content.decode()


def test_home_links_to_login(client):
    assert LOGIN_URL in client.get(reverse("home")).content.decode()


@pytest.mark.parametrize("email", ["alice@example.com", "ALICE@Example.COM"])
def test_active_user_logs_in_with_any_case(client, active_user, email):
    session = client.session
    session["marker"] = "before-login"
    session.save()
    old_key = session.session_key
    response = post_login(client, email)
    assert response.status_code == 302
    assert response.url == reverse("home")
    assert client.session.session_key != old_key
    assert client.session["_auth_user_id"] == str(active_user.pk)


def test_wrong_password_and_unknown_email_get_same_neutral_error(client, active_user):
    wrong = post_login(client, "alice@example.com", "wrong-password-123")
    unknown = post_login(client, "nobody@example.com", "wrong-password-123")
    for response in (wrong, unknown):
        assert response.status_code == 200
        assert INVALID in response.content.decode()
        assert "_auth_user_id" not in client.session
    assert wrong.context["form"].errors == unknown.context["form"].errors


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (User.Status.PENDING, "Your account is not active yet."),
        (User.Status.SUSPENDED, "Your account is suspended. Contact an administrator."),
    ],
)
def test_inactive_user_with_correct_password_gets_specific_message(
    client, make_user, status, message
):
    make_user(status=status)
    response = post_login(client, "alice@example.com")
    assert response.status_code == 200
    assert message in response.content.decode()
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("status", [User.Status.PENDING, User.Status.SUSPENDED])
def test_inactive_user_with_wrong_password_gets_neutral_message(client, make_user, status):
    make_user(status=status)
    response = post_login(client, "alice@example.com", "wrong-password-123")
    content = response.content.decode()
    assert INVALID in content
    assert "suspended" not in content and "not active yet" not in content


def test_deactivated_user_with_correct_password_gets_neutral_message(client, make_user):
    make_user(status=User.Status.DEACTIVATED)
    response = post_login(client, "alice@example.com")
    assert response.status_code == 200
    assert INVALID in response.content.decode()


def test_lockout_after_five_failures_for_same_email_and_ip(client, active_user):
    for _ in range(4):
        assert post_login(client, "alice@example.com", "wrong-password-123").status_code == 200
    # Axes answers the 5th failure itself with the lockout page.
    assert post_login(client, "ALICE@example.com", "wrong-password-123").status_code == 429
    locked = post_login(client, "Alice@Example.com")
    assert locked.status_code == 429
    assert "Too many requests" in locked.content.decode()
    assert "_auth_user_id" not in client.session
    other_ip = post_login(client, "alice@example.com", ip="203.0.113.7")
    assert other_ip.status_code == 302


def test_lockout_page_in_french(client, active_user):
    for _ in range(5):
        post_login(client, "alice@example.com", "wrong-password-123")
    locked = post_login(client, "alice@example.com", HTTP_ACCEPT_LANGUAGE="fr")
    assert locked.status_code == 429
    assert b'<html lang="fr"' in locked.content


def test_success_resets_failure_count(client, active_user):
    for _ in range(4):
        post_login(client, "alice@example.com", "wrong-password-123")
    assert post_login(client, "alice@example.com").status_code == 302
    client.post(reverse("accounts:logout"))
    for _ in range(4):
        post_login(client, "alice@example.com", "wrong-password-123")
    assert post_login(client, "alice@example.com").status_code == 302


def test_authenticated_user_is_redirected_away_from_login(client, active_user):
    client.force_login(active_user)
    response = client.get(LOGIN_URL)
    assert response.status_code == 302
    assert response.url == reverse("home")


def test_logout_requires_post(client, active_user):
    client.force_login(active_user)
    assert client.get(reverse("accounts:logout")).status_code == 405
    assert "_auth_user_id" in client.session
    response = client.post(reverse("accounts:logout"))
    assert response.status_code == 302
    assert response.url == LOGIN_URL
    assert "_auth_user_id" not in client.session


def test_home_shows_logout_form_when_authenticated(client, active_user):
    client.force_login(active_user)
    content = client.get(reverse("home")).content.decode()
    assert f'action="{reverse("accounts:logout")}"' in content
    assert 'method="post"' in content


def test_login_logout_and_failure_are_audited(client, active_user):
    post_login(client, "alice@example.com", "wrong-password-123")
    post_login(client, "ghost@example.com", "wrong-password-123")
    post_login(client, "alice@example.com")
    client.post(reverse("accounts:logout"))
    events = list(AuditEvent.objects.order_by("id").values_list("action", "target_id", "actor"))
    uid = str(active_user.public_id)
    assert events == [
        ("auth.login_failed", uid, None),
        ("auth.login_failed", "unknown", None),
        ("auth.login", uid, active_user.pk),
        ("auth.logout", uid, active_user.pk),
    ]
    failed = AuditEvent.objects.filter(action="auth.login_failed").first()
    assert failed.changes == {}
    assert failed.target_type == "accounts.user"


# --- EmailBackend -------------------------------------------------------------


def _request():
    return RequestFactory().post(LOGIN_URL, REMOTE_ADDR="198.51.100.1")


def test_backend_is_case_insensitive(active_user):
    backend = EmailBackend()
    assert backend.authenticate(None, username="ALICE@example.com", password=PASSWORD) == (
        active_user
    )


def test_backend_runs_hasher_for_unknown_user():
    backend = EmailBackend()
    with mock.patch.object(User, "set_password", autospec=True) as set_password:
        assert backend.authenticate(None, username="ghost@example.com", password="x") is None
    set_password.assert_called_once()


def test_backend_rejects_non_active_statuses(make_user):
    backend = EmailBackend()
    for i, status in enumerate(["pending", "suspended", "deactivated"]):
        user = make_user(f"u{i}@example.com", status=status)
        assert not backend.user_can_authenticate(user)
        assert backend.authenticate(None, username=user.email, password=PASSWORD) is None


@pytest.mark.parametrize("mode", ["local", "mixed"])
def test_local_login_allowed_in_local_and_mixed_modes(settings, active_user, mode):
    settings.AUTH_MODE = mode
    assert authenticate(_request(), username="alice@example.com", password=PASSWORD)


def test_local_login_refused_in_oidc_mode(settings, active_user):
    settings.AUTH_MODE = "oidc"
    settings.BREAK_GLASS_EMAIL = ""
    assert authenticate(_request(), username="alice@example.com", password=PASSWORD) is None


def test_break_glass_account_can_log_in_in_oidc_mode(settings, client, active_user):
    settings.AUTH_MODE = "oidc"
    settings.BREAK_GLASS_EMAIL = "Alice@Example.com"
    assert post_login(client, "alice@example.com").status_code == 302


def test_status_hint_hidden_when_local_login_disabled(settings, client, make_user):
    settings.AUTH_MODE = "oidc"
    settings.BREAK_GLASS_EMAIL = ""
    make_user(status=User.Status.SUSPENDED)
    response = post_login(client, "alice@example.com")
    assert INVALID in response.content.decode()


def test_axes_username_is_normalised():
    request = RequestFactory().post("/", {"username": " Alice@Example.COM "})
    assert axes_username(request, None) == "alice@example.com"
    assert axes_username(request, {"username": "BOB@x.org"}) == "bob@x.org"
    assert axes_username(RequestFactory().post("/"), None) is None


def test_lockout_uses_proxy_aware_client_ip(client, active_user, settings):
    settings.NUM_PROXIES = 1
    proxy = "10.0.0.2"
    for _ in range(5):
        post_login(
            client,
            "alice@example.com",
            "wrong-password",
            ip=proxy,
            HTTP_X_FORWARDED_FOR="198.51.100.9",
        )
    other_client = post_login(
        client, "alice@example.com", ip=proxy, HTTP_X_FORWARDED_FOR="203.0.113.4"
    )
    assert other_client.status_code == 302
