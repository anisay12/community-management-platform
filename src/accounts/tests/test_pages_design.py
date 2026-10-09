"""Account, profile, home and error pages are built on the design system."""

import pytest
from django.contrib.auth.models import Group
from django.test import RequestFactory
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import User
from accounts.roles import Role
from accounts.tokens import activation_token_generator, password_reset_token_generator
from core.tests.helpers import assert_single_h1
from core.views_errors import server_error

pytestmark = pytest.mark.django_db

APP_CSS = "core/dist/app.css"


def _uid_token(user, generator):
    return urlsafe_base64_encode(force_bytes(user.pk)), generator.make_token(user)


def _assert_design_system_page(response, status=200):
    assert response.status_code == status
    assert_single_h1(response)
    assert APP_CSS in response.content.decode()


ANONYMOUS_PAGES = [
    "accounts:login",
    "accounts:password_reset",
    "accounts:password_reset_done",
    "accounts:password_reset_complete",
]


@pytest.mark.parametrize("name", ANONYMOUS_PAGES)
def test_anonymous_pages_use_design_system_without_main_navigation(client, name):
    response = client.get(reverse(name))
    _assert_design_system_page(response)
    html = response.content.decode()
    assert "data-nav-key=" not in html
    assert 'aria-label="Main"' not in html


def test_login_page_has_primary_button_and_labelled_fields(client):
    html = client.get(reverse("accounts:login")).content.decode()
    assert "btn btn-primary" in html
    assert 'class="form-label"' in html
    assert 'class="field"' not in html


def test_activation_pages(client, make_user):
    user = User.objects.create_user("bob@example.com", first_name="Bob", last_name="Martin")
    uid, token = _uid_token(user, activation_token_generator)
    _assert_design_system_page(client.get(reverse("accounts:activate", args=[uid, token])))
    invalid = client.get(reverse("accounts:activate", args=[uid, "bad-token"]))
    assert invalid.status_code in (200, 400)
    assert_single_h1(invalid)


def test_activation_password_help_renders_as_a_real_list(client):
    user = User.objects.create_user("bob@example.com", first_name="Bob", last_name="Martin")
    uid, token = _uid_token(user, activation_token_generator)
    html = client.get(reverse("accounts:activate", args=[uid, token])).content.decode()
    assert "<ul>" in html and "<li>" in html
    assert "&lt;ul&gt;" not in html


def test_password_reset_confirm_help_renders_as_a_real_list(client, make_user):
    user = make_user("carol@example.com")
    uid, token = _uid_token(user, password_reset_token_generator)
    response = client.get(reverse("accounts:password_reset_confirm", args=[uid, token]))
    follow = client.get(response["Location"]) if response.status_code == 302 else response
    _assert_design_system_page(follow)
    html = follow.content.decode()
    assert "<ul>" in html and "&lt;ul&gt;" not in html


def test_password_reset_confirm_invalid_link(client):
    response = client.get(reverse("accounts:password_reset_confirm", args=["bad", "bad-token"]))
    _assert_design_system_page(response)


def test_mfa_pages(client, make_user):
    admin = make_user("admin@example.com")
    admin.groups.add(Group.objects.get(name=Role.FUNCTIONAL_ADMIN))
    client.force_login(admin)
    setup = client.get(reverse("accounts:mfa_setup"))
    _assert_design_system_page(setup)
    assert "data-nav-key=" not in setup.content.decode()
    from django_otp.plugins.otp_totp.models import TOTPDevice

    TOTPDevice.objects.create(user=admin, name="default", confirmed=True)
    _assert_design_system_page(client.get(reverse("accounts:mfa_verify")))


def test_login_error_uses_form_errors_summary(client, make_user):
    make_user("dave@example.com")
    response = client.post(
        reverse("accounts:login"), {"username": "dave@example.com", "password": "wrong"}
    )
    assert "tl-error-summary" in response.content.decode()


@pytest.mark.parametrize(
    "name",
    [
        "accounts:profile_me",
        "accounts:profile_edit",
        "accounts:preferences",
        "accounts:data_export",
    ],
)
def test_signed_in_pages(client, make_user, name):
    client.force_login(make_user("erin@example.com"))
    response = client.get(reverse(name), follow=True)
    _assert_design_system_page(response)
    assert 'data-nav-key="home"' in response.content.decode()


def test_profile_uses_avatar_component(client, make_user):
    user = make_user("erin@example.com")
    client.force_login(user)
    html = client.get(reverse("accounts:profile_me"), follow=True).content.decode()
    assert "tl-avatar" in html


def test_own_profile_shows_role_badge(client, make_user):
    user = make_user("frank@example.com")
    user.groups.add(Group.objects.get(name=Role.COMMUNITY_CREATOR))
    client.force_login(user)
    html = client.get(reverse("accounts:profile_me"), follow=True).content.decode()
    assert "tl-role-badge" in html
    assert "Community creator" in html


def test_other_profile_does_not_show_role_badge(client, make_user):
    owner = make_user("jo@example.com")
    owner.groups.add(Group.objects.get(name=Role.COMMUNITY_CREATOR))
    client.force_login(make_user("kim@example.com"))
    response = client.get(reverse("accounts:profile_detail", args=[owner.public_id]))
    if response.status_code == 200:
        assert "tl-role-badge" not in response.content.decode()


def test_data_export_page_has_empty_state_when_no_export(client, make_user):
    client.force_login(make_user("gina@example.com"))
    html = client.get(reverse("accounts:data_export")).content.decode()
    assert "btn btn-primary" in html
    assert "tl-empty-state" in html


def test_home_anonymous_and_authenticated(client, make_user):
    anonymous = client.get(reverse("home"))
    _assert_design_system_page(anonymous)
    client.force_login(make_user("hank@example.com"))
    signed_in = client.get(reverse("home"))
    _assert_design_system_page(signed_in)
    assert "tl-empty-state" in signed_in.content.decode()
    assert reverse("accounts:profile_me") in signed_in.content.decode()


def test_404_anonymous_offers_sign_in_and_has_no_navigation(client):
    response = client.get("/does-not-exist/")
    _assert_design_system_page(response, 404)
    html = response.content.decode()
    assert reverse("accounts:login") in html
    assert "data-nav-key=" not in html


def test_404_signed_in_does_not_offer_sign_in(client, make_user):
    client.force_login(make_user("ivy@example.com"))
    response = client.get("/does-not-exist/")
    _assert_design_system_page(response, 404)
    assert reverse("accounts:login") not in response.content.decode()


@pytest.mark.urls("core.tests.urls_errors")
def test_403_and_429_pages(client):
    _assert_design_system_page(client.get("/forbidden/"), 403)
    _assert_design_system_page(client.get("/too-many/"), 429)


def test_static_500_is_styled_with_dark_mode_and_no_template_syntax():
    request = RequestFactory().get("/boom")
    request.request_id = "req-1"
    body = server_error(request).content.decode()
    assert "prefers-color-scheme: dark" in body
    assert "--tl-color-bg" in body
    assert "{%" not in body and "{{" not in body
    assert body.count("<h1") == 1
