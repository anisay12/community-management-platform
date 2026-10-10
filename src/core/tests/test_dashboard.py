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


# --- Documents (L5) -----------------------------------------------------------------------


def _document(community, owner, title="A document", scan_status="clean", **extra):
    from documents.models import Document, DocumentVersion

    document = Document.objects.create(
        community=community, owner=owner, owner_display="Owner", title=title, **extra
    )
    version = DocumentVersion.objects.create(
        document=document,
        number=1,
        version_label="1",
        storage_key=f"documents/{document.pk}",
        original_filename="f.pdf",
        mime_type="application/pdf",
        size=1,
        sha256="0" * 64,
        scan_status=scan_status,
        is_reference=True,
    )
    document.current_version = version
    document.save(update_fields=["current_version"])
    return document


def test_member_sees_recent_resources_of_their_communities(
    client, make_user, make_community, add_member
):
    alice = make_user()
    mine = make_community("Python guild")
    other = make_community("Open forum")
    add_member(mine, alice)
    owner = make_user("owner@example.com")
    _document(mine, owner, title="Mine visible")
    _document(mine, owner, title="Mine pending", scan_status="pending")
    _document(other, owner, title="Not a member there")
    client.force_login(alice)
    html = client.get(reverse("home")).content.decode()
    assert 'data-dashboard="resources"' in html
    assert "Mine visible" in html
    assert "Mine pending" not in html
    assert "Not a member there" not in html
    assert 'data-dashboard="documents-review"' not in html
    assert 'data-dashboard="scan-errors"' not in html


def test_lead_sees_documents_to_review_with_reason(client, make_user, make_community, add_member):
    lead = make_user()
    community = make_community()
    add_member(community, lead, role=CommunityMembership.Role.MODERATOR)
    owner = make_user("owner@example.com")
    _document(
        community, owner, title="Stale guide", review_due_at=timezone.now() - timedelta(days=1)
    )
    _document(
        community, owner, title="Fresh guide", review_due_at=timezone.now() + timedelta(days=9)
    )
    client.force_login(lead)
    html = client.get(reverse("home")).content.decode()
    assert 'data-dashboard="documents-review"' in html
    assert "Stale guide" in html and "Review date passed" in html
    assert (
        "Fresh guide"
        not in html.split('data-dashboard="documents-review"')[1].split("</section>")[0]
    )


def test_admin_sees_latest_scan_errors(
    client, functional_admin, verified_login, make_user, make_community
):
    secret = make_community("Secret", access_mode=Community.AccessMode.INVITE)
    owner = make_user("owner@example.com")
    infected = _document(secret, owner, title="Bad file", scan_status="infected")
    verified_login(client, functional_admin)
    html = client.get(reverse("home")).content.decode()
    assert 'data-dashboard="scan-errors"' in html
    assert "Bad file" in html and "Infected" in html
    # No grant on the private community: listed without a link.
    assert reverse("documents:detail", args=[infected.public_id]) not in html


def test_technical_admin_sees_scan_errors(client, make_user, verified_login):
    technical = make_user("tech@example.com")
    technical.groups.add(Group.objects.get(name=Role.TECHNICAL_ADMIN))
    verified_login(client, technical)
    html = client.get(reverse("home")).content.decode()
    assert 'data-dashboard="scan-errors"' in html
    assert "No scan error." in html
