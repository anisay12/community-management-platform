"""Administration pages are built on the design system."""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from accounts.roles import Role
from core.tests.helpers import assert_single_h1

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client, make_user, verified_login):
    admin = make_user("admin@example.com")
    admin.groups.add(Group.objects.get(name=Role.FUNCTIONAL_ADMIN))
    return verified_login(client, admin)


@pytest.fixture
def target(make_user):
    return make_user("target@example.com", first_name="Tara", last_name="Get")


def _pages(target):
    args = [target.public_id]
    return [
        reverse("manage:user_list"),
        reverse("manage:user_create"),
        reverse("manage:user_import"),
        reverse("manage:user_detail", args=args),
        reverse("manage:user_deactivate_confirm", args=args),
    ]


def test_every_page_has_one_h1_and_tabs(admin_client, target):
    for url in _pages(target):
        response = admin_client.get(url)
        assert response.status_code == 200
        assert_single_h1(response)
        html = response.content.decode()
        assert "nav-tabs" in html
        assert 'class="subnav"' not in html and 'class="filters"' not in html


def test_active_tab_is_marked(admin_client):
    html = admin_client.get(reverse("manage:user_create")).content.decode()
    assert html.count('aria-current="page"') >= 1
    assert f'class="nav-link active" href="{reverse("manage:user_create")}"' in html


def test_list_has_caption_filters_card_and_badges(admin_client, target):
    html = admin_client.get(reverse("manage:user_list")).content.decode()
    assert '<caption class="visually-hidden">' in html
    assert "table-responsive" in html
    assert "tl-toolbar" in html and "tl-section" in html
    assert "badge" in html


def test_list_empty_state_and_pagination_keeps_filters(admin_client, make_user):
    html = admin_client.get(reverse("manage:user_list"), {"q": "zzz-nobody"}).content.decode()
    assert "tl-empty-state" in html
    for i in range(25):
        make_user(f"bulk{i}@example.com", first_name="Bulk")
    html = admin_client.get(reverse("manage:user_list"), {"q": "bulk"}).content.decode()
    assert "q=bulk&amp;page=2" in html


def test_detail_has_breadcrumb_and_role_badges(admin_client, target):
    target.groups.add(Group.objects.get(name=Role.AUDITOR))
    html = admin_client.get(reverse("manage:user_detail", args=[target.public_id])).content.decode()
    assert 'aria-label="Breadcrumb"' in html
    assert "tl-role-badge" in html
    assert "<dl" in html


def test_confirmation_page_is_a_card(admin_client, target):
    html = admin_client.get(
        reverse("manage:user_deactivate_confirm", args=[target.public_id])
    ).content.decode()
    assert "tl-section" in html
    assert "btn btn-danger" in html


def _import(admin_client, text):
    from django.core.files.uploadedfile import SimpleUploadedFile

    upload = SimpleUploadedFile("users.csv", text.encode(), content_type="text/csv")
    admin_client.post(reverse("manage:user_import"), {"file": upload})
    return admin_client.get(reverse("manage:user_import_result"))


def test_import_result_pages(admin_client):
    header = "email,first_name,last_name,unit_code,manager_email"
    failure = _import(admin_client, f"{header}\nbroken,X,Y,,\n")
    assert_single_h1(failure)
    html = failure.content.decode()
    assert '<caption class="visually-hidden">' in html
    assert "table-responsive" in html
    assert "alert-danger" in html
    success = _import(admin_client, f"{header}\nann@example.com,Ann,Lee,,\n")
    assert_single_h1(success)
    assert "alert-success" in success.content.decode()
