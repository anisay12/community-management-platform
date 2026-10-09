"""Single sign-on backend: the identity provider is mocked with claim dictionaries."""

from unittest import mock

import pytest
from axes.models import AccessAttempt
from django.contrib.auth import authenticate
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import SuspiciousOperation
from django.db import IntegrityError
from django.test import RequestFactory

from accounts.models import ExternalIdentity, User
from accounts.oidc import TalanOIDCBackend
from audit.models import AuditEvent

pytestmark = pytest.mark.django_db

CLIENT_ID = "test-client-id"


@pytest.fixture
def backend(oidc_settings):
    return TalanOIDCBackend()


def claims(sub="subject-1", email="alice@example.com", **extra):
    return {"sub": sub, "email": email, "aud": CLIENT_ID, **extra}


def sso_login(backend, user_claims, payload=None):
    """Run the backend's user resolution as the callback would, without any network call."""
    payload = payload if payload is not None else user_claims
    with mock.patch.object(backend, "get_userinfo", return_value=user_claims):
        return backend.get_or_create_user("access-token", "id-token", payload)


def actions(user=None):
    events = AuditEvent.objects.all()
    if user is not None:
        events = events.filter(target_id=str(user.public_id))
    return list(events.order_by("id").values_list("action", flat=True))


# Claims ------------------------------------------------------------------------------


def test_verify_claims_requires_subject_and_email(backend):
    assert backend.verify_claims({"sub": "s", "email": "a@example.com"})
    assert backend.verify_claims({"oid": "o", "email": "a@example.com"})
    assert not backend.verify_claims({"email": "a@example.com"})
    assert not backend.verify_claims({"sub": "s"})
    assert not backend.verify_claims({"sub": "s", "email": "  "})


def test_verify_claims_rejects_an_explicitly_unverified_email(backend):
    assert not backend.verify_claims(
        {"sub": "s", "email": "a@example.com", "email_verified": False}
    )


def test_subject_prefers_the_immutable_object_id(backend, active_user):
    sso_login(backend, claims(sub="pairwise-sub", oid="object-id"))
    identity = ExternalIdentity.objects.get(user=active_user)
    assert identity.subject == "object-id"
    assert identity.provider == "entra"


def test_invalid_claims_are_refused(backend, active_user):
    with pytest.raises(SuspiciousOperation):
        sso_login(backend, {"sub": "subject-1", "aud": CLIENT_ID})
    assert not ExternalIdentity.objects.exists()


def test_userinfo_subject_must_match_the_id_token(backend, active_user):
    with pytest.raises(SuspiciousOperation):
        sso_login(backend, claims(sub="other"), payload=claims(sub="subject-1"))


def test_id_token_audience_must_be_this_client(backend, active_user):
    with pytest.raises(SuspiciousOperation):
        sso_login(backend, claims(), payload=claims(aud="another-app"))
    assert sso_login(backend, claims(), payload=claims(aud=["x", CLIENT_ID])) == active_user


# Linking -----------------------------------------------------------------------------


def test_existing_user_is_linked_by_email_on_first_login(backend, active_user):
    user = sso_login(backend, claims(email="Alice@Example.COM"))
    assert user == active_user
    identity = ExternalIdentity.objects.get(user=active_user)
    assert identity.subject == "subject-1"
    assert identity.last_login_at is not None
    assert actions(active_user) == ["auth.sso_linked", "auth.sso_login"]


def test_linked_user_is_found_by_subject_even_if_the_email_changes(backend, active_user):
    sso_login(backend, claims())
    assert sso_login(backend, claims(email="alice.doe@example.com")) == active_user
    assert ExternalIdentity.objects.filter(user=active_user).count() == 1
    assert actions(active_user).count("auth.sso_linked") == 1


def test_same_email_with_another_subject_does_not_get_the_linked_account(backend, active_user):
    sso_login(backend, claims(sub="subject-1"))
    assert sso_login(backend, claims(sub="intruder")) is None
    assert not ExternalIdentity.objects.filter(subject="intruder").exists()
    assert User.objects.filter(email="alice@example.com").count() == 1


def test_email_link_is_scoped_to_the_provider(backend, active_user):
    ExternalIdentity.objects.create(user=active_user, provider="other-idp", subject="x")
    assert sso_login(backend, claims()) == active_user


# Creation and status -----------------------------------------------------------------


def test_unknown_user_gets_a_pending_account_and_is_refused(backend):
    result = sso_login(
        backend, claims(email="New@Example.com", given_name="Nina", family_name="New")
    )
    assert result is None
    user = User.objects.get(email="new@example.com")
    assert user.status == User.Status.PENDING
    assert (user.first_name, user.last_name) == ("Nina", "New")
    assert not user.has_usable_password()
    assert ExternalIdentity.objects.get(user=user).subject == "subject-1"
    assert actions(user) == ["user.created_from_sso"]


def test_pending_sso_user_stays_refused_on_later_logins(backend):
    sso_login(backend, claims(email="new@example.com"))
    assert sso_login(backend, claims(email="new@example.com")) is None
    assert User.objects.filter(email="new@example.com").count() == 1


@pytest.mark.parametrize("status", [User.Status.SUSPENDED, User.Status.DEACTIVATED])
def test_inactive_user_is_refused(backend, make_user, status):
    user = make_user()
    assert sso_login(backend, claims()) == user
    user.status = status
    user.save()
    assert sso_login(backend, claims()) is None
    assert actions(user).count("auth.sso_login") == 1


def test_inactive_unlinked_user_is_refused(backend, make_user):
    make_user(status=User.Status.SUSPENDED)
    assert sso_login(backend, claims()) is None


def test_concurrent_first_login_is_refused_without_partial_changes(backend, active_user):
    with mock.patch.object(ExternalIdentity.objects, "create", side_effect=IntegrityError):
        assert sso_login(backend, claims()) is None
    assert not AuditEvent.objects.filter(action__startswith="auth.sso").exists()


def _callback_authenticate(claims_):
    """Django's authenticate() as the OIDC callback calls it, with the provider mocked."""
    request = RequestFactory().get("/oidc/callback/", {"code": "c", "state": "s"})
    request.session = SessionStore()
    with (
        mock.patch.object(TalanOIDCBackend, "get_token", return_value={"id_token": "i"}),
        mock.patch.object(TalanOIDCBackend, "verify_token", return_value=claims_),
        mock.patch.object(TalanOIDCBackend, "get_userinfo", return_value=claims_),
    ):
        return authenticate(request=request, nonce="n", code_verifier="v")


def test_refused_sso_attempts_do_not_lock_out_the_ip_address(use_auth_mode, active_user):
    use_auth_mode("sso_only")
    for i in range(6):
        assert _callback_authenticate(claims(sub=f"s{i}", email=f"new{i}@example.com")) is None
    assert not AccessAttempt.objects.exists()
    assert _callback_authenticate(claims()) == active_user
