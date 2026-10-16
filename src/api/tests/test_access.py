import pytest
from django.conf import settings
from django.contrib.auth.models import Group
from django.urls import reverse

from accounts.models import User
from accounts.roles import Role

pytestmark = pytest.mark.django_db

ME = "/api/v1/me/"


def test_me_url_name():
    assert reverse("api:me") == ME


def test_anonymous_gets_401_with_authenticate_header(api_client):
    response = api_client.get(ME)
    assert response.status_code == 401
    assert response.has_header("WWW-Authenticate")


def test_active_employee_reads_me(api_client, active_user):
    api_client.force_login(active_user)
    response = api_client.get(ME)
    assert response.status_code == 200
    body = response.json()
    assert body["public_id"] == str(active_user.public_id)
    assert body["display_name"] == "Alice Doe"
    assert body["requires_mfa"] is False
    assert isinstance(body["roles"], list)


@pytest.mark.parametrize("status", [User.Status.SUSPENDED, User.Status.PENDING])
def test_inactive_account_is_unauthenticated(api_client, make_user, status):
    user = make_user("inactive@example.com", status=status)
    api_client.force_login(user)
    assert api_client.get(ME).status_code == 401


def test_privileged_user_without_mfa_gets_403_not_redirect(api_client, functional_admin):
    api_client.force_login(functional_admin)
    response = api_client.get(ME)
    assert response.status_code == 403
    assert not response.has_header("Location")
    assert response.json()["code"] == "mfa_required"


def test_privileged_user_with_verified_mfa_reads_me(client, functional_admin, verified_login):
    verified_login(client, functional_admin)
    response = client.get(ME)
    assert response.status_code == 200
    assert response.json()["requires_mfa"] is True


@pytest.mark.urls("api.tests.urls_plain")
def test_plain_django_view_under_api_keeps_mfa_redirect(client, functional_admin):
    client.force_login(functional_admin)
    response = client.get("/api/plain/")
    assert response.status_code == 302
    assert "/mfa/" in response["Location"]


def test_ui_pages_keep_mfa_redirect(client, functional_admin):
    client.force_login(functional_admin)
    assert client.get("/").status_code == 302


def test_session_is_the_only_authentication():
    assert settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] == [
        "api.authentication.SessionAuthentication"
    ]


def test_roles_are_listed(api_client, make_user):
    user = make_user("creator@example.com")
    user.groups.add(Group.objects.get(name=Role.COMMUNITY_CREATOR))
    api_client.force_login(user)
    assert str(Role.COMMUNITY_CREATOR) in api_client.get(ME).json()["roles"]
