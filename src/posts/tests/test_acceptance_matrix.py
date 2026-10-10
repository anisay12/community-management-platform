"""Acceptance matrix of the framing (section 4.4), at HTTP level, for the rows L4 delivers.

Rows: view the content of an open and of a private community (a post and the feed), publish
a post / ask a question, moderate (hide, archive), pin and publish an announcement. Columns
are viewers: a non-member employee, each community role, a manager (an employee with direct
reports who is not a member), the functional administrator (with and without an "access as
administrator" grant), the technical administrator and the auditor.

A post the viewer may not open answers 404; a forbidden action on a visible post answers 403.
"""

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.roles import Role
from audit.models import AuditEvent
from communities.models import AdminAccessGrant, Community
from communities.roles import CommunityRole
from organizations.models import Employment, OrganizationUnit
from posts.models import Post

pytestmark = pytest.mark.django_db

OPEN, REQUEST, _INVITE = Community.AccessMode
ROLES = [role.value for role in CommunityRole]  # member … owner
OUTSIDERS = ["non_member", "manager", "functional_admin", "technical_admin", "auditor"]
VIEWERS = [*ROLES, *OUTSIDERS, "functional_admin_grant"]
PRIVILEGED = {
    "functional_admin": Role.FUNCTIONAL_ADMIN,
    "functional_admin_grant": Role.FUNCTIONAL_ADMIN,
    "technical_admin": Role.TECHNICAL_ADMIN,
    "auditor": Role.AUDITOR,
}
# Who reads the content of each community (framing rows 1 and 2).
READERS = {
    "open": {v for v in VIEWERS if v not in {"technical_admin", "auditor"}},
    "private": {*ROLES, "functional_admin_grant"},
}
MODERATORS = {"moderator", "animator", "owner", "functional_admin", "functional_admin_grant"}
PINNERS = {"animator", "owner", "functional_admin", "functional_admin_grant"}


@pytest.fixture
def matrix(make_user, make_community, add_member, verified_login, make_post):
    """An open and an on-request community, a published post by a plain member in each, and
    a logged-in client per viewer."""
    communities = {
        "open": make_community("Open guild", access_mode=OPEN),
        "private": make_community("Private guild", access_mode=REQUEST),
    }
    author = make_user("author@example.com", first_name="Ada", last_name="Author")
    users, clients = {}, {}
    for viewer in VIEWERS:
        user = make_user(f"{viewer}@example.com", first_name=viewer.title(), last_name="Viewer")
        if viewer in PRIVILEGED:
            user.groups.add(Group.objects.get(name=PRIVILEGED[viewer]))
        if viewer in ROLES:
            for community in communities.values():
                add_member(community, user, role=viewer)
        client = Client()
        if viewer in PRIVILEGED:
            verified_login(client, user)
        else:
            client.force_login(user)
        users[viewer], clients[viewer] = user, client
    unit = OrganizationUnit.objects.create(name="Data", code="DATA")
    Employment.objects.create(user=users["non_member"], unit=unit, manager=users["manager"])
    posts = {}
    for key, community in communities.items():
        add_member(community, author)
        AdminAccessGrant.objects.create(
            community=community, user=users["functional_admin_grant"], reason="Moderation case"
        )
        posts[key] = make_post(community, author, title=f"Post in {community.name}")
    return {
        "communities": communities,
        "users": users,
        "clients": clients,
        "author": author,
        "posts": posts,
    }


def _post_url(name, post):
    return reverse(f"posts:{name}", args=[post.community.slug, post.public_id])


def _statuses(matrix, url):
    return {viewer: matrix["clients"][viewer].get(url).status_code for viewer in VIEWERS}


def _refused(kind, viewer):
    """On a post: 403 for a viewer who reads the community content, 404 for one who does not."""
    return 403 if viewer in READERS[kind] else 404


def _refused_in_community(kind, viewer):
    """On a community page (feed, editor): 403 for a viewer who knows the content exists (a
    reader, anyone on an open community, the functional administrator), 404 otherwise."""
    if viewer in READERS[kind] or kind == "open" or viewer == "functional_admin":
        return 403
    return 404


# --- View an open community and its content / the content of a private community ----------


@pytest.mark.parametrize("kind", ["open", "private"])
def test_view_a_post(matrix, kind):
    statuses = _statuses(matrix, _post_url("detail", matrix["posts"][kind]))
    assert statuses == {v: 200 if v in READERS[kind] else 404 for v in VIEWERS}


def test_view_the_feed_of_an_open_community(matrix):
    slug = matrix["communities"]["open"].slug
    statuses = _statuses(matrix, reverse("posts:feed", args=[slug]))
    expected = {
        v: 200 if v in READERS["open"] else _refused_in_community("open", v) for v in VIEWERS
    }
    assert statuses == expected


def test_view_the_feed_of_a_private_community(matrix):
    """Its metadata is public: the feed answers 403 to the functional administrator without a
    grant (who knows every community) and 404 to the other non-members."""
    slug = matrix["communities"]["private"].slug
    statuses = _statuses(matrix, reverse("posts:feed", args=[slug]))
    expected = {
        v: 200 if v in READERS["private"] else _refused_in_community("private", v) for v in VIEWERS
    }
    assert statuses == expected


def test_non_member_gets_404_on_a_private_post(matrix):
    """Framing L4 acceptance: a non-member gets 404 on a private post."""
    post = matrix["posts"]["private"]
    for viewer in ("non_member", "manager", "functional_admin", "technical_admin", "auditor"):
        response = matrix["clients"][viewer].get(_post_url("detail", post))
        assert response.status_code == 404, viewer
        assert post.title not in response.content.decode()


@pytest.mark.parametrize("kind", ["open", "private"])
def test_feeds_list_a_post_only_to_its_readers(matrix, kind):
    post = matrix["posts"][kind]
    for viewer in VIEWERS:
        response = matrix["clients"][viewer].get(reverse("posts:home_feed"))
        assert response.status_code == 200
        # The home feed lists the communities one belongs to.
        assert (post.title in response.content.decode()) == (viewer in ROLES), viewer


# --- Publish a post / ask a question ---------------------------------------------------------


@pytest.mark.parametrize("post_kind", [Post.Kind.DISCUSSION, Post.Kind.QUESTION])
@pytest.mark.parametrize("kind", ["open", "private"])
def test_publish_a_post_or_ask_a_question(matrix, kind, post_kind):
    community = matrix["communities"][kind]
    url = reverse("posts:create", args=[community.slug])
    statuses = {}
    for viewer in VIEWERS:
        title = f"{post_kind} by {viewer}"
        response = matrix["clients"][viewer].post(
            url, {"kind": post_kind, "title": title, "body": "Hello", "action": "publish"}
        )
        statuses[viewer] = response.status_code
        created = Post.objects.filter(community=community, title=title, kind=post_kind)
        assert created.exists() == (viewer in ROLES), viewer
    expected = {v: 302 if v in ROLES else _refused_in_community(kind, v) for v in VIEWERS}
    assert statuses == expected


# --- Moderate (hide, archive) ----------------------------------------------------------------


@pytest.mark.parametrize("kind", ["open", "private"])
def test_hide_a_post(matrix, kind, make_post):
    community = matrix["communities"][kind]
    allowed = MODERATORS & READERS[kind]
    for viewer in VIEWERS:
        post = make_post(community, matrix["author"], title=f"Hide me ({viewer})")
        response = matrix["clients"][viewer].post(
            _post_url("hide", post), {"confirmed": "1", "reason": "Off topic"}
        )
        expected = 302 if viewer in allowed else _refused(kind, viewer)
        assert response.status_code == expected, viewer
        post.refresh_from_db()
        assert (post.status == Post.Status.HIDDEN) == (viewer in allowed), viewer


@pytest.mark.parametrize("kind", ["open", "private"])
def test_archive_a_post(matrix, kind, make_post):
    community = matrix["communities"][kind]
    allowed = MODERATORS & READERS[kind]
    for viewer in VIEWERS:
        post = make_post(community, matrix["author"], title=f"Archive me ({viewer})")
        response = matrix["clients"][viewer].post(_post_url("archive", post), {"confirmed": "1"})
        expected = 302 if viewer in allowed else _refused(kind, viewer)
        assert response.status_code == expected, viewer
        post.refresh_from_db()
        assert (post.status == Post.Status.ARCHIVED) == (viewer in allowed), viewer


def test_hiding_by_a_moderator_is_audited(matrix):
    """Framing L4 acceptance: hiding by a moderator is audited, with its reason."""
    post = matrix["posts"]["private"]
    moderator = matrix["users"]["moderator"]
    response = matrix["clients"]["moderator"].post(
        _post_url("hide", post), {"confirmed": "1", "reason": "Confidential figures"}
    )
    assert response.status_code == 302
    event = AuditEvent.objects.get(action="post.hidden", target_id=str(post.public_id))
    assert event.actor == moderator
    assert event.community == post.community
    assert event.changes["reason"] == "Confidential figures"
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN
    assert post.hidden_by == moderator
    # Everyone else but the author and the moderators now gets 404.
    assert matrix["clients"]["member"].get(_post_url("detail", post)).status_code == 404


# --- Pin, publish an announcement ------------------------------------------------------------


@pytest.mark.parametrize("kind", ["open", "private"])
def test_pin_a_post(matrix, kind):
    post = matrix["posts"][kind]
    allowed = PINNERS & READERS[kind]
    for viewer in VIEWERS:
        response = matrix["clients"][viewer].post(_post_url("pin", post))
        expected = 302 if viewer in allowed else _refused(kind, viewer)
        assert response.status_code == expected, viewer
        post.refresh_from_db()
        assert (post.pinned_at is not None) == (viewer in allowed), viewer
        Post.objects.filter(pk=post.pk).update(pinned_at=None, pinned_by=None)


@pytest.mark.parametrize("kind", ["open", "private"])
def test_publish_an_announcement(matrix, kind):
    community = matrix["communities"][kind]
    url = reverse("posts:create", args=[community.slug])
    allowed = PINNERS & READERS[kind]
    statuses = {}
    for viewer in VIEWERS:
        title = f"Announcement by {viewer}"
        response = matrix["clients"][viewer].post(
            url, {"kind": "announcement", "title": title, "body": "News", "action": "publish"}
        )
        statuses[viewer] = response.status_code
        created = Post.objects.filter(
            community=community, title=title, status=Post.Status.PUBLISHED
        )
        assert created.exists() == (viewer in allowed), viewer
    expected = {v: 302 if v in allowed else _refused_in_community(kind, v) for v in VIEWERS}
    assert statuses == expected


def test_functional_admin_is_offered_the_announcement_editor(matrix):
    """The functional administrator is no member, yet may publish an announcement: the
    "New post" button leads to an editor offering announcements only."""
    slug = matrix["communities"]["open"].slug
    client = matrix["clients"]["functional_admin"]
    feed = client.get(reverse("posts:feed", args=[slug])).content.decode()
    assert reverse("posts:create", args=[slug]) in feed
    response = client.get(reverse("posts:create", args=[slug]))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'value="announcement"' in html
    assert 'value="discussion"' not in html


def test_expired_grant_closes_a_private_post_again(matrix):
    AdminAccessGrant.objects.filter(user=matrix["users"]["functional_admin_grant"]).update(
        expires_at=timezone.now()
    )
    post = matrix["posts"]["private"]
    response = matrix["clients"]["functional_admin_grant"].get(_post_url("detail", post))
    assert response.status_code == 404
