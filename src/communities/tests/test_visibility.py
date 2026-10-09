"""Visibility matrix: the selector and the metadata/content policies must agree."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from accounts.models import User
from communities.models import AdminAccessGrant, Community
from communities.policies import can_view_content, can_view_metadata

pytestmark = pytest.mark.django_db

OPEN, REQUEST, INVITE = Community.AccessMode
ACTIVE, SUSPENDED, ARCHIVED = Community.Status
KINDS = {
    "open": {"access_mode": OPEN},
    "request": {"access_mode": REQUEST},
    "invite_listed": {"access_mode": INVITE, "listed": True},
    "invite_unlisted": {"access_mode": INVITE},
}
VIEWERS = [
    "non_member",
    "member",
    "animator",
    "admin_no_grant",
    "admin_grant",
    "admin_expired_grant",
    "suspended_employee",
    "anonymous",
]

# (metadata, content) per viewer for an ACTIVE community of each kind.
EXPECTED = {
    "open": {
        "non_member": (True, True),
        "member": (True, True),
        "animator": (True, True),
        "admin_no_grant": (True, False),
        "admin_grant": (True, True),
        "admin_expired_grant": (True, False),
        "suspended_employee": (False, False),
        "anonymous": (False, False),
    },
    "request": {
        "non_member": (True, False),
        "member": (True, True),
        "animator": (True, True),
        "admin_no_grant": (True, False),
        "admin_grant": (True, True),
        "admin_expired_grant": (True, False),
        "suspended_employee": (False, False),
        "anonymous": (False, False),
    },
}
EXPECTED["invite_listed"] = EXPECTED["request"]
EXPECTED["invite_unlisted"] = {**EXPECTED["request"], "non_member": (False, False)}


@pytest.fixture
def viewers(make_user, functional_admin, add_member):
    def _build(community):
        member = make_user("member@example.com")
        animator = make_user("animator@example.com")
        suspended = make_user("suspended@example.com")
        add_member(community, member)
        add_member(community, animator, role="animator")
        add_member(community, suspended)
        suspended.status = User.Status.SUSPENDED
        suspended.save()
        suspended.refresh_from_db()
        granted = make_user("granted@example.com")
        granted.groups.add(*functional_admin.groups.all())
        expired = make_user("expired@example.com")
        expired.groups.add(*functional_admin.groups.all())
        reason = "Moderation investigation"
        AdminAccessGrant.objects.create(community=community, user=granted, reason=reason)
        AdminAccessGrant.objects.create(
            community=community,
            user=expired,
            reason=reason,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        return {
            "non_member": make_user("outsider@example.com"),
            "member": member,
            "animator": animator,
            "admin_no_grant": functional_admin,
            "admin_grant": granted,
            "admin_expired_grant": expired,
            "suspended_employee": suspended,
            "anonymous": AnonymousUser(),
        }

    return _build


def _check(community, users, expected):
    for viewer, (metadata, content) in expected.items():
        user = users[viewer]
        visible = Community.objects.visible_to(user).filter(pk=community.pk).exists()
        assert visible is metadata, (viewer, "selector")
        assert can_view_metadata(user, community) is metadata, (viewer, "metadata")
        assert can_view_content(user, community) is content, (viewer, "content")


@pytest.mark.parametrize("kind", list(KINDS))
def test_active_community_matrix(kind, make_community, viewers):
    community = make_community(**KINDS[kind])
    _check(community, viewers(community), EXPECTED[kind])


@pytest.mark.parametrize("kind", list(KINDS))
def test_suspended_community_matrix_matches_active(kind, make_community, viewers):
    community = make_community(status=SUSPENDED, **KINDS[kind])
    _check(community, viewers(community), EXPECTED[kind])


@pytest.mark.parametrize("kind", list(KINDS))
def test_archived_community_only_for_members_and_admins(kind, make_community, viewers):
    community = make_community(status=ARCHIVED, **KINDS[kind])
    expected = {**EXPECTED[kind], "non_member": (False, False)}
    _check(community, viewers(community), expected)


def test_grant_is_per_community(make_community, functional_admin):
    first = make_community("First", access_mode=REQUEST)
    second = make_community("Second", access_mode=REQUEST)
    AdminAccessGrant.objects.create(community=first, user=functional_admin, reason="x" * 12)
    assert can_view_content(functional_admin, first)
    assert not can_view_content(functional_admin, second)


def test_superuser_counts_as_functional_admin(make_community, make_user):
    community = make_community(access_mode=INVITE)
    root = make_user("root@example.com", is_superuser=True)
    assert can_view_metadata(root, community)
    assert Community.objects.visible_to(root).filter(pk=community.pk).exists()
    assert not can_view_content(root, community)


def test_selector_returns_no_duplicates(make_community, make_user, add_member):
    community = make_community()
    user = make_user()
    add_member(community, user)
    assert list(Community.objects.visible_to(user)) == [community]


def test_selectors(make_community, make_user, category):
    from communities.selectors import active_categories, visible_communities

    community = make_community()
    assert list(visible_communities(make_user())) == [community]
    category.is_active = False
    category.save()
    assert category not in active_categories()
