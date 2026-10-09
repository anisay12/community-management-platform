"""HTTP tests of the read side: catalogue, community page, members tab, admin access."""

from datetime import timedelta

import pytest
from django.contrib.postgres.search import SearchVector
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditEvent
from communities.models import AdminAccessGrant, Community, CommunityCategory
from communities.roles import CommunityRole as Role
from core.tests.helpers import assert_single_h1

pytestmark = pytest.mark.django_db

OPEN, REQUEST, INVITE = Community.AccessMode
CATALOGUE = "/communities/"


@pytest.fixture
def employee(make_user):
    return make_user("emp@example.com", first_name="Eve", last_name="Employee")


@pytest.fixture
def employee_client(client, employee):
    client.force_login(employee)
    return client


@pytest.fixture
def admin_client(client, functional_admin, verified_login):
    return verified_login(client, functional_admin)


def _names(response):
    return [community.name for community in response.context["page_obj"]]


# --- Catalogue -----------------------------------------------------------------------------


def test_catalogue_requires_login(client):
    response = client.get(CATALOGUE)
    assert response.status_code == 302


def test_catalogue_lists_visible_communities(employee_client, make_community):
    make_community("Open one", access_mode=OPEN)
    make_community("Request one", access_mode=REQUEST)
    make_community("Listed invite", access_mode=INVITE, listed=True)
    make_community("Hidden invite", access_mode=INVITE)
    make_community("Old", status=Community.Status.ARCHIVED)
    response = employee_client.get(CATALOGUE)
    assert response.status_code == 200
    assert_single_h1(response)
    assert sorted(_names(response)) == ["Listed invite", "Open one", "Request one"]
    assert reverse("communities:detail", args=["open-one"]) in response.content.decode()


def test_unlisted_invite_never_appears_for_non_member(employee_client, make_community, category):
    hidden = make_community("Secret circle", access_mode=INVITE)
    Community.objects.filter(pk=hidden.pk).update(search_vector=SearchVector("name"))
    for query in (
        {},
        {"q": "Secret"},
        {"q": "circle"},
        {"category": category.slug},
        {"access_mode": INVITE},
        {"mine": "on"},
        {"sort": "members"},
        {"sort": "name"},
    ):
        response = employee_client.get(CATALOGUE, query)
        assert "Secret circle" not in _names(response), query
        assert "Secret circle" not in response.content.decode(), query


def test_unlisted_invite_appears_for_member(employee_client, employee, make_community, add_member):
    hidden = make_community("Secret circle", access_mode=INVITE)
    add_member(hidden, employee)
    assert _names(employee_client.get(CATALOGUE, {"mine": "on"})) == ["Secret circle"]


def test_catalogue_filters(employee_client, employee, make_community, add_member):
    other = CommunityCategory.objects.create(name="Other", slug="other")
    first = make_community("Alpha", access_mode=OPEN)
    make_community("Beta", access_mode=REQUEST)
    make_community("Gamma", access_mode=OPEN, category=other)
    add_member(first, employee)
    assert _names(employee_client.get(CATALOGUE, {"category": "other"})) == ["Gamma"]
    assert _names(employee_client.get(CATALOGUE, {"access_mode": REQUEST})) == ["Beta"]
    assert _names(employee_client.get(CATALOGUE, {"mine": "on"})) == ["Alpha"]
    # Invalid filters are ignored rather than failing.
    response = employee_client.get(CATALOGUE, {"category": "nope", "sort": "bogus"})
    assert response.status_code == 200
    assert len(_names(response)) == 3


def test_catalogue_sorts(employee_client, make_community):
    now = timezone.now()
    make_community("Bravo", member_count=5, last_activity_at=now - timedelta(days=3))
    make_community("alpha", member_count=1, last_activity_at=now)
    make_community("Charlie", member_count=9, last_activity_at=now - timedelta(days=1))
    assert _names(employee_client.get(CATALOGUE)) == ["alpha", "Charlie", "Bravo"]
    assert _names(employee_client.get(CATALOGUE, {"sort": "members"})) == [
        "Charlie",
        "Bravo",
        "alpha",
    ]
    assert _names(employee_client.get(CATALOGUE, {"sort": "name"})) == ["alpha", "Bravo", "Charlie"]


def test_catalogue_search_uses_vector_and_name(employee_client, make_community):
    make_community("Kubernetes club", description="containers")
    make_community("Data guild", tagline="Pipelines and warehouses")
    make_community("Design")
    Community.objects.update(
        search_vector=SearchVector("name", weight="A", config="simple")
        + SearchVector("tagline", weight="B", config="simple")
    )
    assert _names(employee_client.get(CATALOGUE, {"q": "pipelines"})) == ["Data guild"]
    # Name fallback (partial word, not a lexeme).
    assert _names(employee_client.get(CATALOGUE, {"q": "kuber"})) == ["Kubernetes club"]


def test_catalogue_search_matches_simple_config_vector(employee_client, make_community):
    # The services build ``search_vector`` with the "simple" configuration (no stemming):
    # the query must use it too, or "running" (stemmed to "run" in English) never matches.
    make_community("Joggers", tagline="Running every morning")
    make_community("Other", tagline="Reading")
    Community.objects.update(search_vector=SearchVector("tagline", config="simple"))
    assert _names(employee_client.get(CATALOGUE, {"q": "running"})) == ["Joggers"]


def test_catalogue_pagination(employee_client, make_community):
    for index in range(30):
        make_community(f"Community {index:02d}")
    first = employee_client.get(CATALOGUE, {"sort": "name"})
    assert len(_names(first)) == 24
    second = employee_client.get(CATALOGUE, {"sort": "name", "page": 2})
    assert len(_names(second)) == 6
    assert "sort=name&amp;page=2" in first.content.decode()


def test_catalogue_empty_state(employee_client):
    response = employee_client.get(CATALOGUE, {"q": "nothing"})
    assert "tl-empty-state" in response.content.decode()


def test_catalogue_query_count(
    employee_client, employee, make_community, add_member, django_assert_max_num_queries
):
    for index in range(30):
        community = make_community(f"Community {index:02d}")
        add_member(community, employee)
    with django_assert_max_num_queries(12):
        response = employee_client.get(CATALOGUE, {"mine": "on"})
    assert response.status_code == 200


# --- Navigation ----------------------------------------------------------------------------


def test_navigation_entry(employee_client):
    html = employee_client.get(CATALOGUE).content.decode()
    assert 'data-nav-key="communities"' in html


# --- Community page and visibility matrix --------------------------------------------------

# (detail status, members status) for an active community.
MATRIX = {
    "open": {"non_member": (200, 200), "member": (200, 200), "animator": (200, 200),
             "admin": (200, 200)},
    "request": {"non_member": (200, 403), "member": (200, 200), "animator": (200, 200),
                "admin": (200, 403)},
    "invite": {"non_member": (404, 404), "member": (200, 200), "animator": (200, 200),
               "admin": (200, 403)},
}  # fmt: skip


@pytest.mark.parametrize("mode", ["open", "request", "invite"])
@pytest.mark.parametrize("viewer", ["non_member", "member", "animator", "admin"])
def test_visibility_matrix(
    client, make_user, functional_admin, verified_login, make_community, add_member, mode, viewer
):
    community = make_community("Matrix", access_mode=mode)
    if viewer == "admin":
        verified_login(client, functional_admin)
    else:
        user = make_user("viewer@example.com")
        if viewer != "non_member":
            add_member(community, user, Role.ANIMATOR if viewer == "animator" else Role.MEMBER)
        client.force_login(user)
    detail = client.get(reverse("communities:detail", args=[community.slug]))
    members = client.get(reverse("communities:members", args=[community.slug]))
    assert (detail.status_code, members.status_code) == MATRIX[mode][viewer]
    if detail.status_code == 200:
        assert_single_h1(detail)
    if members.status_code == 200:
        assert_single_h1(members)


def test_unknown_community_is_404(employee_client):
    assert employee_client.get("/communities/nope/").status_code == 404


def test_detail_header_about_and_tabs(employee_client, make_user, make_community, add_member):
    community = make_community(
        "Python guild",
        access_mode=REQUEST,
        member_count=2,
        description="**Bold** <script>alert(1)</script>",
        rules="- be kind",
        objectives="[docs](https://example.com)",
    )
    add_member(community, make_user("lead@example.com", first_name="Lea", last_name="Lead"),
               Role.OWNER)  # fmt: skip
    html = employee_client.get(reverse("communities:detail", args=[community.slug])).content
    html = html.decode()
    assert "Everything Python" in html
    assert "Test category" in html
    assert "Lea Lead" in html
    assert "<strong>Bold</strong>" in html
    assert "<script>alert" not in html
    assert "<li>be kind</li>" in html
    assert 'rel="noopener noreferrer nofollow"' in html
    # Members tab hidden when the content is not visible.
    assert reverse("communities:members", args=[community.slug]) not in html
    assert "Access as administrator" not in html


def test_members_tab_lists_members_with_roles(employee_client, employee, make_user, make_community,
                                              add_member):  # fmt: skip
    community = make_community("Open", access_mode=OPEN)
    add_member(community, make_user("bob@example.com", first_name="Bob", last_name="Lead"),
               Role.OWNER)  # fmt: skip
    add_member(community, employee)
    response = employee_client.get(reverse("communities:members", args=[community.slug]))
    html = response.content.decode()
    assert "Bob Lead" in html and "Eve Employee" in html
    assert 'tl-role-badge text-bg-dark">Lead</span>' in html
    assert 'tl-role-badge text-bg-secondary">Member</span>' in html
    assert "tl-community-role-badge" not in html
    detail = employee_client.get(reverse("communities:detail", args=[community.slug]))
    assert reverse("communities:members", args=[community.slug]) in detail.content.decode()


def test_member_names_never_expose_email(employee_client, employee, make_user, make_community,
                                        add_member):  # fmt: skip
    community = make_community("Open", access_mode=OPEN)
    nameless = make_user("nameless.lead@example.com", first_name="", last_name="")
    add_member(community, nameless, Role.OWNER)
    former = make_user("former@example.com", anonymized_at=timezone.now())
    add_member(community, former)
    add_member(community, employee)
    for name in ("members", "detail"):
        html = employee_client.get(reverse(f"communities:{name}", args=[community.slug]))
        html = html.content.decode()
        assert "nameless.lead@example.com" not in html
        assert "nameless.lead" in html
    html = employee_client.get(reverse("communities:members", args=[community.slug]))
    html = html.content.decode()
    assert "Former employee" in html and "former@example.com" not in html


# --- Administrator access ------------------------------------------------------------------


def test_admin_without_grant_sees_access_link(admin_client, make_community):
    community = make_community("Closed", access_mode=REQUEST)
    html = admin_client.get(reverse("communities:detail", args=[community.slug])).content.decode()
    assert reverse("communities:admin_access", args=[community.slug]) in html


def test_admin_access_form_page(admin_client, make_community):
    community = make_community("Closed", access_mode=REQUEST)
    response = admin_client.get(reverse("communities:admin_access", args=[community.slug]))
    assert response.status_code == 200
    assert_single_h1(response)


def test_admin_access_forbidden_to_employees(employee_client, make_community):
    community = make_community("Open", access_mode=OPEN)
    url = reverse("communities:admin_access", args=[community.slug])
    assert employee_client.get(url).status_code == 403
    assert employee_client.post(url, {"reason": "x" * 20}).status_code == 403


def test_admin_access_404_for_invisible(employee_client, make_community):
    community = make_community("Hidden", access_mode=INVITE)
    url = reverse("communities:admin_access", args=[community.slug])
    assert employee_client.get(url).status_code == 404


def test_admin_access_reason_required(admin_client, make_community):
    community = make_community("Closed", access_mode=REQUEST)
    url = reverse("communities:admin_access", args=[community.slug])
    response = admin_client.post(url, {"reason": "short"})
    assert response.status_code == 200
    assert response.context["form"].errors["reason"]
    assert not AdminAccessGrant.objects.exists()


def test_admin_with_grant_sees_content(admin_client, functional_admin, make_community):
    community = make_community("Closed", access_mode=REQUEST)
    AdminAccessGrant.objects.create(community=community, user=functional_admin, reason="x" * 12)
    response = admin_client.get(reverse("communities:members", args=[community.slug]))
    assert response.status_code == 200


def test_admin_access_flow(admin_client, functional_admin, make_community):
    community = make_community("Closed", access_mode=REQUEST)
    members_url = reverse("communities:members", args=[community.slug])
    assert admin_client.get(members_url).status_code == 403
    url = reverse("communities:admin_access", args=[community.slug])
    response = admin_client.post(url, {"reason": "Investigating a report"})
    assert response.status_code == 302
    assert response.url == reverse("communities:detail", args=[community.slug])
    assert AdminAccessGrant.objects.filter(community=community, user=functional_admin).exists()
    assert AuditEvent.objects.filter(action="community.admin_access", community=community).exists()
    assert admin_client.get(members_url).status_code == 200


def test_admin_access_domain_error_is_a_form_error(admin_client, make_community, monkeypatch):
    from communities import services
    from core.errors import DomainError

    def refuse(**kwargs):
        raise DomainError("forbidden", "Refused for a test reason.")

    monkeypatch.setattr(services, "grant_admin_access", refuse)
    community = make_community("Closed", access_mode=REQUEST)
    url = reverse("communities:admin_access", args=[community.slug])
    response = admin_client.post(url, {"reason": "Investigating a report"})
    assert response.status_code == 200
    assert "Refused for a test reason." in response.content.decode()
    assert not AdminAccessGrant.objects.exists()


def test_catalogue_links_to_invitations_and_creation_request(employee_client):
    page = employee_client.get(reverse("communities:catalogue")).content.decode()
    assert reverse("communities:my_invitations") in page
    assert reverse("communities:creation_request") in page
    assert f'href="{reverse("communities:create")}"' not in page


def test_catalogue_links_to_create_for_creators(admin_client):
    page = admin_client.get(reverse("communities:catalogue")).content.decode()
    assert f'href="{reverse("communities:create")}"' in page
    assert reverse("communities:creation_request") not in page
