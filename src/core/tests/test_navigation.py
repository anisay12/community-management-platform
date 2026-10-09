import re

import pytest
from django.contrib import messages
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.messages.storage.fallback import FallbackStorage
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils import translation

from accounts.roles import Role
from core import navigation
from core.navigation import NavItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def clean_registry():
    saved = dict(navigation._registry)
    yield
    navigation._registry.clear()
    navigation._registry.update(saved)


def _user(make_user, email, *roles):
    user = make_user(email)
    for role in roles:
        user.groups.add(Group.objects.get(name=role))
    return user


def _request(path="/", user=None):
    request = RequestFactory().get(path)
    request.user = user or AnonymousUser()
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def test_employee_sees_home_and_user_menu_only(client, make_user):
    client.force_login(_user(make_user, "emp@example.com", Role.EMPLOYEE))
    html = client.get("/").content.decode()
    assert 'data-nav-key="home"' in html
    assert "user-menu" in html
    assert 'data-nav-key="manage_users"' not in html
    assert 'data-nav-key="audit_log"' not in html


def test_functional_admin_sees_administration(client, functional_admin, verified_login):
    verified_login(client, functional_admin)
    html = client.get("/").content.decode()
    assert 'data-nav-key="manage_users"' in html
    assert 'data-nav-key="audit_log"' in html  # functional admins have the functional audit scope


def test_auditor_sees_audit_log_not_administration(client, make_user, verified_login):
    verified_login(client, _user(make_user, "aud@example.com", Role.AUDITOR))
    html = client.get("/audit/").content.decode()
    assert 'data-nav-key="audit_log"' in html
    assert 'data-nav-key="manage_users"' not in html


def test_anonymous_navbar_has_sign_in_and_no_user_menu():
    html = render_to_string("components/navbar.html", request=_request())
    assert "user-menu" not in html
    assert reverse("accounts:login") in html
    assert 'data-nav-key="home"' not in html


def test_register_rejects_duplicate_key(clean_registry):
    item = NavItem(key="dup", label="Dup", url_name="home", icon="x", order=1)
    navigation.register(item)
    with pytest.raises(ValueError):
        navigation.register(item)


def test_unresolvable_url_name_is_skipped(clean_registry, make_user):
    navigation.register(
        NavItem(key="ghost", label="Ghost", url_name="nope:nowhere", icon="x", order=1)
    )
    request = _request(user=make_user())
    assert "ghost" not in [i.key for i in navigation.items_for(request)]
    assert 'data-nav-key="ghost"' not in render_to_string("components/navbar.html", request=request)


def test_items_are_ordered(clean_registry, make_user):
    navigation._registry.clear()
    navigation.register(NavItem(key="c", label="C", url_name="home", icon="x", order=30))
    navigation.register(NavItem(key="a", label="A", url_name="home", icon="x", order=-5))
    navigation.register(NavItem(key="b2", label="B2", url_name="home", icon="x", order=10))
    navigation.register(NavItem(key="b1", label="B1", url_name="home", icon="x", order=10))
    keys = [i.key for i in navigation.items_for(_request(user=make_user()))]
    assert keys == ["a", "b1", "b2", "c"]


def test_aria_current_only_on_current_item(client, make_user, verified_login):
    auditor = _user(make_user, "aud@example.com", Role.AUDITOR)
    verified_login(client, auditor)
    html = client.get("/audit/").content.decode()
    assert html.count('aria-current="page"') == 1
    match = re.search(r'<a[^>]*aria-current="page"[^>]*>', html)
    assert 'data-nav-key="audit_log"' in match.group(0)


def test_home_is_current_only_on_root(client, make_user, verified_login):
    verified_login(client, _user(make_user, "aud@example.com", Role.AUDITOR))
    html = client.get("/audit/").content.decode()
    assert not re.search(r'data-nav-key="home"[^>]*aria-current', html)


def test_page_landmarks(client, make_user):
    client.force_login(make_user())
    html = client.get("/").content.decode()
    assert html.count("<header") == 1
    assert html.count('<main id="main"') == 1
    assert html.count("<footer") == 1
    for nav in re.findall(r"<nav\b[^>]*>", html):
        assert "aria-label=" in nav
    body = html.split("<body", 1)[1]
    first_link = re.search(r"<a\b[^>]*>", body).group(0)
    assert 'href="#main"' in first_link


def test_toggler_and_offcanvas_share_one_item_list(client, make_user):
    client.force_login(make_user())
    html = client.get("/").content.decode()
    assert html.count('data-nav-key="home"') == 1
    assert 'data-bs-toggle="offcanvas"' in html


def test_success_message_is_in_live_region():
    request = _request()
    messages.success(request, "Saved")
    html = render_to_string("components/messages.html", request=request)
    assert "aria-live=" in html
    assert "Saved" in html
    assert 'role="alert"' not in html


def test_error_message_uses_alert_role():
    request = _request()
    messages.error(request, "Broken")
    html = render_to_string("components/messages.html", request=request)
    assert 'role="alert"' in html
    assert "Broken" in html


def test_french_navigation_labels(client, make_user):
    client.force_login(make_user())
    with translation.override("fr"):
        html = client.get("/", headers={"accept-language": "fr"}).content.decode()
    assert "Accueil" in html
    assert "Mon profil" in html


def test_base_minimal_has_no_navigation(client):
    html = render_to_string("base_minimal.html", request=_request())
    assert "data-nav-key" not in html
    assert '<main id="main"' in html
    assert 'href="#main"' in html
