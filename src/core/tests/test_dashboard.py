from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

from accounts.roles import Role
from audit.models import AuditEvent
from communities.models import Community, CommunityCreationRequest, CommunityMembership
from posts.models import Bookmark, ContentReport, Post
from posts.rendering import render_body

pytestmark = pytest.mark.django_db

SECTIONS = ("moderation", "administration", "audit")


def _post(community, author, title="A post", status=Post.Status.PUBLISHED):
    return Post.objects.create(
        community=community,
        author=author,
        title=title,
        body="Body",
        body_html=render_body("Body"),
        status=status,
        published_at=timezone.now() if status == Post.Status.PUBLISHED else None,
        author_display=author.get_full_name(),
    )


def _sections(response) -> set[str]:
    html = response.content.decode()
    return {name for name in SECTIONS if f'data-dashboard="{name}"' in html}


def test_anonymous_keeps_welcome(client):
    html = client.get(reverse("home")).content.decode()
    assert "tl-hero" in html
    assert reverse("accounts:login") in html
    assert "data-dashboard" not in html


def test_member_sees_communities_and_latest_posts(client, make_user, make_community, add_member):
    alice = make_user()
    mine = make_community("Python guild")
    other = make_community("Secret club", access_mode=Community.AccessMode.INVITE)
    add_member(mine, alice)
    post = _post(mine, alice, title="Visible title")
    _post(other, make_user("bob@example.com"), title="Hidden title")
    _post(mine, alice, title="Draft title", status=Post.Status.DRAFT)
    Bookmark.objects.create(user=alice, post=post)
    client.force_login(alice)
    response = client.get(reverse("home"))
    html = response.content.decode()
    assert response.status_code == 200
    assert html.count("<h1") == 1
    assert "Hello Alice" in html
    assert reverse("communities:detail", args=[mine.slug]) in html
    assert reverse("communities:detail", args=[other.slug]) not in html
    assert "Visible title" in html
    assert "Hidden title" not in html
    assert "Draft title" not in html
    assert reverse("posts:home_feed") in html
    assert reverse("posts:bookmarks") in html
    assert _sections(response) == set()


def test_moderator_sees_queue_counts(client, make_user, make_community, add_member):
    mod = make_user("mod@example.com")
    author = make_user("author@example.com")
    community = make_community()
    add_member(community, mod, role=CommunityMembership.Role.MODERATOR)
    add_member(community, author)
    _post(community, author, status=Post.Status.PENDING_REVIEW)
    reported = _post(community, author)
    ContentReport.objects.create(
        reporter=mod, community=community, post=reported, reason=ContentReport.Reason.INAPPROPRIATE
    )
    client.force_login(mod)
    response = client.get(reverse("home"))
    html = response.content.decode()
    assert _sections(response) == {"moderation"}
    assert reverse("posts:moderation_queue", args=[community.slug]) in html
    assert "1 post pending review" in html
    assert "1 open report" in html
    client.force_login(author)
    assert _sections(client.get(reverse("home"))) == set()


def test_functional_admin_sees_administration(
    client, functional_admin, verified_login, make_user, category
):
    make_user("pending@example.com", status="pending")
    CommunityCreationRequest.objects.create(
        requester=functional_admin, name="New one", category=category
    )
    verified_login(client, functional_admin)
    response = client.get(reverse("home"))
    html = response.content.decode()
    assert "administration" in _sections(response)
    assert reverse("manage:user_list") in html
    assert reverse("manage:creation_request_list") in html
    assert "1 pending community creation request" in html


def test_auditor_sees_recent_audit_events(client, make_user, verified_login):
    auditor = make_user("auditor@example.com")
    auditor.groups.add(Group.objects.get(name=Role.AUDITOR))
    AuditEvent.objects.create(action="x.recent", target_type="t", target_id="1")
    AuditEvent.objects.create(
        action="x.old",
        target_type="t",
        target_id="2",
        created_at=timezone.now() - timedelta(days=30),
    )
    verified_login(client, auditor)
    response = client.get(reverse("home"))
    html = response.content.decode()
    assert _sections(response) == {"audit"}
    assert reverse("audit:event_list") in html


def test_populated_dashboard_query_count_is_bounded(
    client,
    functional_admin,
    verified_login,
    make_user,
    make_community,
    add_member,
    django_assert_max_num_queries,
):
    functional_admin.groups.add(Group.objects.get(name=Role.AUDITOR))
    verified_login(client, functional_admin)
    for index in range(8):
        community = make_community(f"Community {index}")
        add_member(community, functional_admin, role=CommunityMembership.Role.MODERATOR)
        author = make_user(f"author{index}@example.com")
        for number in range(3):
            post = _post(community, author, title=f"Post {index}-{number}")
            Bookmark.objects.create(user=functional_admin, post=post)
        _post(community, author, status=Post.Status.PENDING_REVIEW)
    with django_assert_max_num_queries(20):
        response = client.get(reverse("home"))
    assert set(_sections(response)) == set(SECTIONS)
