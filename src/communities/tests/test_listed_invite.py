"""A listed invite-only community shows only its title to non-members (spec section 6)."""

import pytest
from django.contrib.auth.models import Group
from django.contrib.postgres.search import SearchVector
from django.test import Client
from django.urls import reverse

from accounts.roles import Role
from communities import policies
from communities.models import Community

pytestmark = pytest.mark.django_db

HIDDEN = ["Zebracorn tagline", "Quokkadesc", "Narwhalobj", "Axolotlrule", "Leadington"]


@pytest.fixture
def listed(make_user, make_community, add_member):
    community = make_community(
        "Listed circle",
        access_mode=Community.AccessMode.INVITE,
        listed=True,
        tagline=HIDDEN[0],
        description=f"About {HIDDEN[1]} things",
        objectives=f"Grow {HIDDEN[2]}",
        rules=f"Respect {HIDDEN[3]}",
    )
    lead = make_user("lead@example.com", first_name=HIDDEN[4], last_name="Lead")
    add_member(community, lead, role="owner")
    for index in range(6):
        add_member(community, make_user(f"m{index}@example.com"))
    Community.objects.filter(pk=community.pk).update(
        search_vector=SearchVector("name", "tagline", "description", config="simple")
    )
    return community


def _client(user):
    client = Client()
    client.force_login(user)
    return client


def _assert_no_leak(html):
    for word in HIDDEN:
        assert word not in html
    assert "7 members" not in html


def test_policy_full_metadata(listed, make_user, add_member):
    outsider = make_user("out@example.com")
    member = make_user("in@example.com")
    add_member(listed, member)
    assert policies.can_view_metadata(outsider, listed)
    assert not policies.can_view_full_metadata(outsider, listed)
    assert policies.can_view_full_metadata(member, listed)


def test_non_member_detail_is_stripped(listed, make_user):
    response = _client(make_user("out@example.com")).get(
        reverse("communities:detail", args=[listed.slug])
    )
    assert response.status_code == 200
    html = response.content.decode()
    assert "Listed circle" in html
    assert "Invitation only" in html
    assert "Membership is by invitation" in html
    _assert_no_leak(html)


@pytest.mark.parametrize("role", [Role.TECHNICAL_ADMIN, Role.AUDITOR])
def test_privileged_non_member_detail_is_stripped(listed, make_user, verified_login, role):
    user = make_user("priv@example.com")
    user.groups.add(Group.objects.get(name=role))
    client = Client()
    verified_login(client, user)
    response = client.get(reverse("communities:detail", args=[listed.slug]))
    assert response.status_code == 200
    _assert_no_leak(response.content.decode())


def test_non_member_members_tab_refused(listed, make_user):
    response = _client(make_user("out@example.com")).get(
        reverse("communities:members", args=[listed.slug])
    )
    assert response.status_code in (403, 404)


def test_member_sees_full_detail(listed, make_user, add_member):
    member = make_user("in@example.com")
    add_member(listed, member)
    html = _client(member).get(reverse("communities:detail", args=[listed.slug])).content.decode()
    for word in HIDDEN:
        assert word in html


def test_functional_admin_sees_full_detail(listed, make_user, verified_login):
    admin = make_user("fa@example.com")
    admin.groups.add(Group.objects.get(name=Role.FUNCTIONAL_ADMIN))
    client = Client()
    verified_login(client, admin)
    html = client.get(reverse("communities:detail", args=[listed.slug])).content.decode()
    for word in HIDDEN:
        assert word in html


def test_catalogue_card_shows_title_only(listed, make_user):
    response = _client(make_user("out@example.com")).get(reverse("communities:catalogue"))
    html = response.content.decode()
    assert "Listed circle" in html
    assert "Invitation only" in html
    _assert_no_leak(html)


@pytest.mark.parametrize("word", ["Quokkadesc", "Zebracorn"])
def test_catalogue_search_does_not_match_hidden_text(listed, make_user, word):
    response = _client(make_user("out@example.com")).get(
        reverse("communities:catalogue"), {"q": word}
    )
    assert "Listed circle" not in {c.name for c in response.context["page_obj"]}


def test_catalogue_search_matches_title_for_non_member(listed, make_user):
    response = _client(make_user("out@example.com")).get(
        reverse("communities:catalogue"), {"q": "circle"}
    )
    assert "Listed circle" in {c.name for c in response.context["page_obj"]}


def test_member_search_matches_description(listed, make_user, add_member):
    member = make_user("in@example.com")
    add_member(listed, member)
    response = _client(member).get(reverse("communities:catalogue"), {"q": "Quokkadesc"})
    assert "Listed circle" in {c.name for c in response.context["page_obj"]}
