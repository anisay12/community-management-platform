"""Visibility matrix: ``Post.objects.visible_to``, ``can_view_post`` and
``get_visible_post_or_404`` must agree for every viewer, community mode and post status."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser, Group
from django.http import Http404
from django.utils import timezone

from accounts.models import User
from accounts.roles import Role
from communities.models import AdminAccessGrant, Community
from posts.models import Post
from posts.policies import can_view_post
from posts.selectors import get_visible_post_or_404

pytestmark = pytest.mark.django_db

MODES = {
    "open": {"access_mode": Community.AccessMode.OPEN},
    "request": {"access_mode": Community.AccessMode.REQUEST},
    "invite": {"access_mode": Community.AccessMode.INVITE, "listed": True},
}
VIEWERS = [
    "anonymous",
    "non_member",
    "member",
    "moderator",
    "admin_no_grant",
    "admin_grant",
    "admin_expired_grant",
    "technical_admin",
    "auditor",
    "suspended_employee",
]
# Who reads the content (published and archived posts) of an active community, per mode.
CONTENT_READERS = {
    "open": {
        "non_member",
        "member",
        "moderator",
        "admin_no_grant",
        "admin_grant",
        "admin_expired_grant",
    },
    "request": {"member", "moderator", "admin_grant"},
}
CONTENT_READERS["invite"] = CONTENT_READERS["request"]
# Who sees pending and hidden posts of others: moderators+ and admins with content access.
MODERATORS = {"moderator", "admin_no_grant", "admin_grant", "admin_expired_grant"}
S = Post.Status


def expected(mode, viewer, status) -> bool:
    if status in (S.PUBLISHED, S.ARCHIVED):
        return viewer in CONTENT_READERS[mode]
    if status == S.DRAFT:
        return False
    return viewer in MODERATORS and viewer in CONTENT_READERS[mode]


def _with_role(make_user, email, role):
    user = make_user(email)
    user.groups.add(Group.objects.get(name=role))
    return user


@pytest.fixture
def world(make_user, make_community, add_member, make_post):
    def _build(mode, status_list=tuple(S)):
        community = make_community(f"Guild {mode}", **MODES[mode])
        author = make_user("author@example.com", first_name="Ann", last_name="Author")
        add_member(community, author)
        viewers = {"anonymous": AnonymousUser()}
        viewers["non_member"] = make_user("outsider@example.com")
        viewers["member"] = make_user("member@example.com")
        add_member(community, viewers["member"])
        viewers["moderator"] = make_user("moderator@example.com")
        add_member(community, viewers["moderator"], role="moderator")
        for name in ("admin_no_grant", "admin_grant", "admin_expired_grant"):
            viewers[name] = _with_role(make_user, f"{name}@example.com", Role.FUNCTIONAL_ADMIN)
        AdminAccessGrant.objects.create(
            community=community, user=viewers["admin_grant"], reason="Moderation check"
        )
        AdminAccessGrant.objects.create(
            community=community,
            user=viewers["admin_expired_grant"],
            reason="Moderation check",
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        viewers["technical_admin"] = _with_role(make_user, "tech@example.com", Role.TECHNICAL_ADMIN)
        viewers["auditor"] = _with_role(make_user, "auditor@example.com", Role.AUDITOR)
        suspended = make_user("suspended@example.com")
        add_member(community, suspended, role="moderator")
        User.objects.filter(pk=suspended.pk).update(status=User.Status.SUSPENDED)
        viewers["suspended_employee"] = User.objects.get(pk=suspended.pk)
        posts = {status: make_post(community, author, status=status) for status in status_list}
        return community, author, viewers, posts

    return _build


def _sees(user, post) -> tuple[bool, bool, bool]:
    in_queryset = Post.objects.visible_to(user).filter(pk=post.pk).exists()
    fresh = Post.objects.select_related("community").get(pk=post.pk)
    try:
        get_visible_post_or_404(user, post.community.slug, post.public_id)
        found = True
    except Http404:
        found = False
    return in_queryset, can_view_post(user, fresh), found


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("status", list(S))
def test_visibility_matrix_for_non_authors(world, mode, status):
    _community, _author, viewers, posts = world(mode, (status,))
    post = posts[status]
    for name in VIEWERS:
        want = expected(mode, name, status)
        assert _sees(viewers[name], post) == (want, want, want), (mode, status, name)


@pytest.mark.parametrize("mode", list(MODES))
def test_authors_see_their_own_posts_in_every_status(world, mode):
    _community, author, _viewers, posts = world(mode)
    for status, post in posts.items():
        assert _sees(author, post) == (True, True, True), (mode, status)


def test_suspended_author_sees_nothing(world):
    _community, author, _viewers, posts = world("open")
    User.objects.filter(pk=author.pk).update(status=User.Status.SUSPENDED)
    author = User.objects.get(pk=author.pk)
    for post in posts.values():
        assert _sees(author, post) == (False, False, False)


def test_archived_community_content_is_for_members_only(world):
    community, _author, viewers, posts = world("open", (S.PUBLISHED,))
    community.status = Community.Status.ARCHIVED
    community.save()
    post = posts[S.PUBLISHED]
    assert _sees(viewers["member"], post) == (True, True, True)
    assert _sees(viewers["non_member"], post) == (False, False, False)
    assert _sees(viewers["admin_no_grant"], post) == (False, False, False)


def test_non_member_of_private_community_gets_404(world, client):
    _community, _author, viewers, posts = world("invite", (S.PUBLISHED,))
    outsider = viewers["non_member"]
    assert not Post.objects.visible_to(outsider).exists()
    with pytest.raises(Http404):
        get_visible_post_or_404(outsider, "guild-invite", posts[S.PUBLISHED].public_id)


def test_wrong_community_slug_is_404(world, make_community):
    _community, _author, viewers, posts = world("open", (S.PUBLISHED,))
    make_community("Elsewhere")
    with pytest.raises(Http404):
        get_visible_post_or_404(viewers["member"], "elsewhere", posts[S.PUBLISHED].public_id)
