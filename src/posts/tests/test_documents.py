"""Bookmarks and reports of documents (L5) handled by the posts app: constraints, services,
endpoints (permission matrix), bookmarks page and moderation queue."""

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from audit.models import AuditEvent
from communities.models import Community
from communities.roles import CommunityRole
from core.errors import DomainError
from documents.models import Document, DocumentVersion
from documents.tests import conftest as document_fixtures
from notifications.models import Notification
from posts import services_interactions as services
from posts.models import Bookmark, BookmarkCollection, ContentReport
from posts.services_moderation import (
    DISMISS,
    RESOLVE,
    RESOLVE_AND_ARCHIVE,
    RESOLVE_AND_HIDE,
    decide_reports,
)

pytestmark = pytest.mark.django_db

# The ``make_document`` fixture of the documents tests, shared here.
make_document = document_fixtures.make_document

OPEN, INVITE = Community.AccessMode.OPEN, Community.AccessMode.INVITE
HTMX = {"HX-Request": "true"}
Reason = ContentReport.Reason


@pytest.fixture
def community(make_community):
    return make_community("Python guild", access_mode=OPEN)


@pytest.fixture
def owner(make_user, community, add_member):
    user = make_user("owner@example.com", first_name="Olga", last_name="Owner")
    add_member(community, user, CommunityRole.CONTRIBUTOR)
    return user


@pytest.fixture
def member(make_user, community, add_member):
    user = make_user("member@example.com", first_name="Mia", last_name="Member")
    add_member(community, user)
    return user


@pytest.fixture
def moderator(make_user, community, add_member):
    user = make_user("mod@example.com", first_name="Mo", last_name="Derator")
    add_member(community, user, CommunityRole.MODERATOR)
    return user


@pytest.fixture
def outsider(make_user):
    return make_user("outsider@example.com", first_name="Out", last_name="Sider")


@pytest.fixture
def document(make_document, community, owner):
    return make_document(community, owner, title="Style guide", doc_type="guide")


@pytest.fixture
def private_document(make_document, make_community, owner, add_member):
    secret = make_community("Secret guild", access_mode=INVITE)
    add_member(secret, owner, CommunityRole.CONTRIBUTOR)
    return make_document(secret, owner, title="Secret plans")


def _code(callable_, **kwargs):
    with pytest.raises(DomainError) as error:
        callable_(**kwargs)
    return error.value.code


def _bookmark_url(document):
    return reverse("posts:bookmark_document", args=[document.public_id])


def _report_url(document):
    return reverse("posts:report_document", args=[document.public_id])


def _queue(community):
    return reverse("posts:moderation_queue", args=[community.slug])


def _report(document, reporter, reason=Reason.OUTDATED, **extra):
    return ContentReport.objects.create(
        reporter=reporter, document=document, community=document.community, reason=reason, **extra
    )


# --- Constraints ----------------------------------------------------------------------------


def test_bookmark_targets_exactly_one_item(document, member, make_post, community, owner):
    post = make_post(community, owner)
    with pytest.raises(IntegrityError), transaction.atomic():
        Bookmark.objects.create(user=member, post=post, document=document)
    with pytest.raises(IntegrityError), transaction.atomic():
        Bookmark.objects.create(user=member)
    Bookmark.objects.create(user=member, document=document)
    with pytest.raises(IntegrityError), transaction.atomic():
        Bookmark.objects.create(user=member, document=document)
    Bookmark.objects.create(user=member, post=post)  # a post and a document side by side


def test_report_targets_exactly_one_item(document, member, make_post, community, owner):
    post = make_post(community, owner)
    with pytest.raises(IntegrityError), transaction.atomic():
        ContentReport.objects.create(
            reporter=member, community=community, reason="spam", post=post, document=document
        )
    _report(document, member)
    with pytest.raises(IntegrityError), transaction.atomic():
        _report(document, member)  # one open report per reporter and document
    _report(document, member, status=ContentReport.Status.DISMISSED)


def test_reported_document_is_protected(document, member):
    from django.db.models import ProtectedError

    _report(document, member)
    with pytest.raises(ProtectedError):
        document.delete()


def test_bookmarks_go_with_the_document(document, member):
    Bookmark.objects.create(user=member, document=document)
    Document.objects.filter(pk=document.pk).delete()
    assert not Bookmark.objects.exists()


# --- Bookmark services ----------------------------------------------------------------------


def test_set_document_bookmark_is_idempotent(document, member):
    collection = BookmarkCollection.objects.create(user=member, name="Reading")
    assert services.set_document_bookmark(
        actor=member, document=document, present=True, collection=collection
    )
    assert services.set_document_bookmark(actor=member, document=document, present=True)
    bookmark = Bookmark.objects.get(user=member)
    assert bookmark.document == document and bookmark.collection == collection
    services.move_bookmark(actor=member, bookmark=bookmark, collection=None)
    assert Bookmark.objects.get(user=member).collection is None
    assert services.set_document_bookmark(actor=member, document=document, present=False) is False
    assert not Bookmark.objects.exists()


def test_bookmark_refused_without_read_access(private_document, outsider, member, document):
    assert (
        _code(
            services.set_document_bookmark, actor=outsider, document=private_document, present=True
        )
        == "forbidden"
    )
    other = BookmarkCollection.objects.create(user=outsider, name="Not mine")
    assert (
        _code(
            services.set_document_bookmark,
            actor=member,
            document=document,
            present=True,
            collection=other,
        )
        == "forbidden"
    )


def test_archived_document_is_not_bookmarked_but_can_be_removed(document, member, owner):
    Bookmark.objects.create(user=member, document=document)
    document.status = Document.Status.ARCHIVED
    document.save()
    assert (
        _code(services.set_document_bookmark, actor=owner, document=document, present=True)
        == "forbidden"
    )
    services.set_document_bookmark(actor=member, document=document, present=False)
    assert not Bookmark.objects.exists()


# --- Report services ------------------------------------------------------------------------


def test_report_document_alerts_moderators(
    document, member, moderator, owner, community, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        report = services.report_document(
            actor=member, document=document, reason=Reason.OUTDATED, details=" Old API "
        )
    assert report.document == document and report.post is None and report.comment is None
    assert report.community == community and report.details == "Old API"
    alerts = Notification.objects.filter(category="report_document")
    assert [n.recipient for n in alerts] == [moderator]
    assert alerts.get().target_id == str(document.public_id)
    # Never hidden automatically, whatever the number of reports.
    document.refresh_from_db()
    assert document.status == Document.Status.ACTIVE


def test_many_reports_do_not_hide_a_document(document, make_user, settings):
    settings.POSTS_REPORT_AUTOHIDE_THRESHOLD = 2
    for index in range(3):
        services.report_document(
            actor=make_user(f"r{index}@example.com"), document=document, reason=Reason.SPAM
        )
    document.refresh_from_db()
    assert document.status == Document.Status.ACTIVE
    assert ContentReport.objects.filter(document=document).count() == 3


def test_report_document_refusals(document, private_document, owner, member, outsider):
    report = services.report_document
    assert _code(report, actor=owner, document=document, reason="spam") == "own_content"
    assert _code(report, actor=outsider, document=private_document, reason="spam") == "forbidden"
    assert _code(report, actor=member, document=document, reason="nope") == "invalid_reason"
    assert (
        _code(report, actor=member, document=document, reason="spam", details="x" * 2001)
        == "body_too_long"
    )
    report(actor=member, document=document, reason="spam")
    assert _code(report, actor=member, document=document, reason="other") == "already_reported"


def test_report_of_a_document_under_scan_is_refused(make_document, community, owner, member):
    pending = make_document(community, owner, scan_status=DocumentVersion.ScanStatus.PENDING)
    code = _code(services.report_document, actor=member, document=pending, reason="spam")
    assert code == "forbidden"  # not readable yet


def test_moderator_cannot_report_an_archived_document(document, moderator):
    document.status = Document.Status.ARCHIVED
    document.save()
    code = _code(services.report_document, actor=moderator, document=document, reason="spam")
    assert code == "invalid_state"


# --- Decisions ------------------------------------------------------------------------------


def test_resolve_closes_every_open_report_and_audits(document, member, outsider, moderator):
    first = _report(document, member)
    second = _report(document, outsider, Reason.SPAM)
    assert decide_reports(actor=moderator, report=first, decision=RESOLVE, note="Updated") == 2
    for report in (first, second):
        report.refresh_from_db()
        assert report.status == ContentReport.Status.RESOLVED
        assert report.handled_by == moderator and report.resolution_note == "Updated"
    assert AuditEvent.objects.filter(action="report.resolved").count() == 2
    document.refresh_from_db()
    assert document.status == Document.Status.ACTIVE


def test_dismiss(document, member, moderator):
    report = _report(document, member)
    assert decide_reports(actor=moderator, report=report, decision=DISMISS) == 1
    report.refresh_from_db()
    assert report.status == ContentReport.Status.DISMISSED
    assert AuditEvent.objects.filter(action="report.dismissed").exists()


def test_resolve_and_archive(document, member, moderator):
    report = _report(document, member)
    decide_reports(actor=moderator, report=report, decision=RESOLVE_AND_ARCHIVE, note="Obsolete")
    document.refresh_from_db()
    assert document.status == Document.Status.ARCHIVED
    assert document.archived_by == moderator and document.archived_at is not None
    event = AuditEvent.objects.get(action="document.archive")
    assert event.target_id == str(document.public_id)
    assert event.community_id == document.community_id
    report.refresh_from_db()
    assert report.status == ContentReport.Status.RESOLVED


def test_hiding_does_not_apply_to_documents(document, member, moderator, make_post, owner):
    report = _report(document, member)
    code = _code(
        decide_reports, actor=moderator, report=report, decision=RESOLVE_AND_HIDE, note="x"
    )
    assert code == "invalid_state"
    code = _code(services.resolve_report, actor=moderator, report=report, note="x", hide=True)
    assert code == "invalid_state"
    report.refresh_from_db()
    assert report.status == ContentReport.Status.OPEN
    post = make_post(document.community, owner)
    post_report = ContentReport.objects.create(
        reporter=member, post=post, community=post.community, reason="spam"
    )
    code = _code(decide_reports, actor=moderator, report=post_report, decision=RESOLVE_AND_ARCHIVE)
    assert code == "invalid_state"


def test_decisions_need_a_document_moderator(document, member, outsider, owner):
    report = _report(document, outsider)
    for actor in (member, owner):
        assert _code(decide_reports, actor=actor, report=report, decision=DISMISS) == "forbidden"
    report.refresh_from_db()
    assert report.status == ContentReport.Status.OPEN


# --- Bookmark endpoint ----------------------------------------------------------------------


def test_bookmark_endpoint_requires_login(client, document):
    response = client.post(_bookmark_url(document), {"present": "1"})
    assert response.status_code == 302 and "login" in response["Location"]


def test_bookmark_endpoint_hidden_document_is_404(client, private_document, outsider):
    client.force_login(outsider)
    assert client.post(_bookmark_url(private_document), {"present": "1"}).status_code == 404


def test_bookmark_endpoint_is_post_only_and_validates(client, member, document):
    client.force_login(member)
    assert client.get(_bookmark_url(document)).status_code == 405
    assert client.post(_bookmark_url(document), {"present": "x"}).status_code == 400


def test_bookmark_endpoint_htmx_toggle(client, member, document):
    client.force_login(member)
    response = client.post(_bookmark_url(document), {"present": "1"}, headers=HTMX)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'aria-pressed="true"' in html and 'name="present" value="0"' in html
    assert f'id="bookmark-document-{document.public_id}"' in html
    assert Bookmark.objects.filter(user=member, document=document).exists()
    response = client.post(_bookmark_url(document), {"present": "0"}, headers=HTMX)
    assert 'aria-pressed="false"' in response.content.decode()
    assert not Bookmark.objects.exists()


def test_bookmark_endpoint_plain_request_redirects_to_the_document(client, member, document):
    client.force_login(member)
    response = client.post(_bookmark_url(document), {"present": "1"})
    assert response.status_code == 302
    assert response["Location"].endswith(f"/documents/{document.public_id}/")


def test_bookmark_endpoint_refusal_is_a_toast(client, owner, document):
    document.status = Document.Status.ARCHIVED
    document.save()
    client.force_login(owner)  # still sees the archived document, cannot bookmark it
    response = client.post(_bookmark_url(document), {"present": "1"}, headers=HTMX)
    assert response.status_code == 204 and response["HX-Redirect"]
    assert not Bookmark.objects.exists()


# --- Report endpoint ------------------------------------------------------------------------


def test_report_endpoint_requires_login(client, document):
    response = client.get(_report_url(document))
    assert response.status_code == 302 and "login" in response["Location"]


def test_report_endpoint_hidden_document_is_404(client, private_document, outsider):
    client.force_login(outsider)
    assert client.get(_report_url(private_document)).status_code == 404
    assert client.post(_report_url(private_document), {"reason": "spam"}).status_code == 404


def test_report_endpoint_own_document_is_403(client, owner, document):
    client.force_login(owner)
    assert client.get(_report_url(document)).status_code == 403
    assert client.post(_report_url(document), {"reason": "spam"}).status_code == 403
    assert not ContentReport.objects.exists()


def test_report_form_page(client, member, document):
    client.force_login(member)
    response = client.get(_report_url(document))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Style guide" in html and 'name="reason"' in html
    assert f'action="{_report_url(document)}"' in html


def test_report_endpoint_creates_the_report(client, member, moderator, document):
    client.force_login(member)
    response = client.post(_report_url(document), {"reason": "outdated", "details": "v1 API"})
    assert response.status_code == 302
    assert response["Location"].endswith(f"/documents/{document.public_id}/")
    report = ContentReport.objects.get()
    assert report.document == document and report.reason == "outdated"


def test_report_endpoint_htmx(client, member, document):
    client.force_login(member)
    response = client.post(_report_url(document), {"reason": "spam"}, headers=HTMX)
    assert response.status_code == 200
    assert "moderators have been informed" in response.content.decode()
    response = client.post(_report_url(document), {}, headers=HTMX)
    assert response.status_code == 204  # invalid form: error toast


def test_report_endpoint_already_reported(client, member, document):
    _report(document, member)
    client.force_login(member)
    response = client.get(_report_url(document))
    assert response.status_code == 302  # error toast, back to the document
    response = client.post(_report_url(document), {"reason": "spam"})
    assert response.status_code == 302
    assert ContentReport.objects.count() == 1


def test_report_url_does_not_shadow_the_generic_report_route(client, member, make_post, owner):
    post = make_post(owner.community_memberships.get().community, owner)
    client.force_login(member)
    assert client.get(reverse("posts:report", args=["post", post.public_id])).status_code == 200


# --- Bookmarks page -------------------------------------------------------------------------


def test_bookmarks_page_lists_documents(client, member, document, make_post, owner):
    collection = BookmarkCollection.objects.create(user=member, name="Reading")
    Bookmark.objects.create(user=member, document=document, collection=collection)
    Bookmark.objects.create(user=member, post=make_post(document.community, owner, title="Hi"))
    client.force_login(member)
    html = client.get(reverse("posts:bookmarks")).content.decode()
    assert "Style guide" in html and "Hi" in html
    assert f'href="/documents/{document.public_id}/"' in html
    assert "Python guild" in html and "Guide" in html
    assert reverse("posts:bookmark_document_remove", args=[document.public_id]) in html
    assert reverse("posts:collection_move_document", args=[document.public_id]) in html


def test_bookmarks_page_hides_documents_no_longer_visible(
    client, make_user, make_community, add_member, make_document, owner
):
    secret = make_community("Secret guild", access_mode=INVITE)
    add_member(secret, owner, CommunityRole.CONTRIBUTOR)
    document = make_document(secret, owner, title="Secret plans")
    reader = make_user("reader@example.com")
    membership = add_member(secret, reader)
    Bookmark.objects.create(user=reader, document=document)
    membership.delete()
    client.force_login(reader)
    html = client.get(reverse("posts:bookmarks")).content.decode()
    assert "Content not accessible" in html and "Secret plans" not in html
    remove = reverse("posts:bookmark_document_remove", args=[document.public_id])
    response = client.post(remove)
    assert response["Location"] == reverse("posts:bookmarks")
    assert not Bookmark.objects.exists()


def test_move_document_bookmark(client, member, document, outsider):
    mine = BookmarkCollection.objects.create(user=member, name="Reading")
    theirs = BookmarkCollection.objects.create(user=outsider, name="Theirs")
    Bookmark.objects.create(user=member, document=document)
    client.force_login(member)
    move = reverse("posts:collection_move_document", args=[document.public_id])
    assert client.post(move, {"collection": str(mine.public_id)}).status_code == 302
    assert Bookmark.objects.get().collection == mine
    assert client.post(move, {"collection": str(theirs.public_id)}).status_code == 404
    assert client.post(move, {"collection": ""}).status_code == 302
    assert Bookmark.objects.get().collection is None
    other = reverse("posts:collection_move_document", args=[outsider.public_id])
    assert client.post(other, {"collection": ""}).status_code == 404


def test_bookmarks_page_query_ceiling_with_documents(
    client, member, community, owner, make_document, make_post, django_assert_max_num_queries
):
    collection = BookmarkCollection.objects.create(user=member, name="Reading")
    for index in range(10):
        Bookmark.objects.create(
            user=member, document=make_document(community, owner, title=f"D{index}")
        )
        Bookmark.objects.create(
            user=member, post=make_post(community, owner, title=f"P{index}"), collection=collection
        )
    client.force_login(member)
    with django_assert_max_num_queries(13):
        response = client.get(reverse("posts:bookmarks"))
    assert response.status_code == 200


# --- Moderation queue -----------------------------------------------------------------------


def test_queue_lists_document_reports(client, moderator, member, outsider, document, community):
    _report(document, member, details="Uses the old API")
    _report(document, outsider, Reason.SPAM)
    client.force_login(moderator)
    response = client.get(_queue(community))
    assert response.status_code == 200
    (group,) = response.context["page_obj"].object_list
    assert group.is_document and group.target == document and group.report_count == 2
    assert group.post is None and not group.auto_hidden and group.can_archive
    html = response.content.decode()
    assert f'href="/documents/{document.public_id}/"' in html
    assert "Uses the old API" in html and 'value="resolve_archive"' in html
    assert 'value="resolve_hide"' not in html
    assert response.context["reported_count"] == 1


def test_queue_mixes_posts_and_documents(
    client, moderator, member, document, community, make_post, owner
):
    post = make_post(community, owner)
    ContentReport.objects.create(reporter=member, post=post, community=community, reason="spam")
    _report(document, member)
    client.force_login(moderator)
    response = client.get(_queue(community))
    groups = response.context["page_obj"].object_list
    assert {type(group.target) for group in groups} == {type(post), Document}
    assert response.context["reported_count"] == 2


def test_queue_archive_decision(client, moderator, member, document, community):
    report = _report(document, member)
    client.force_login(moderator)
    response = client.post(
        reverse("posts:report_decide", args=[report.public_id]),
        {"decision": "resolve_archive", "note": "Obsolete"},
    )
    assert response.status_code == 302 and response["Location"] == _queue(community)
    document.refresh_from_db()
    assert document.status == Document.Status.ARCHIVED
    assert AuditEvent.objects.filter(action="document.archive", actor=moderator).exists()
    report.refresh_from_db()
    assert report.status == ContentReport.Status.RESOLVED
    # Handled reports stay listed, without the archive action.
    response = client.get(_queue(community))
    (group,) = response.context["page_obj"].object_list
    assert not group.is_open and not group.can_archive


@pytest.mark.parametrize("decision", ["resolve", "dismiss"])
def test_queue_resolve_and_dismiss_document_reports(
    client, moderator, member, outsider, document, decision
):
    report = _report(document, member)
    other = _report(document, outsider)
    client.force_login(moderator)
    response = client.post(
        reverse("posts:report_decide", args=[report.public_id]), {"decision": decision}
    )
    assert response.status_code == 302
    statuses = set(ContentReport.objects.values_list("status", flat=True))
    assert statuses == {"resolved" if decision == "resolve" else "dismissed"}
    other.refresh_from_db()
    assert other.handled_by == moderator


def test_queue_hide_decision_on_a_document_is_refused(client, moderator, member, document):
    report = _report(document, member)
    client.force_login(moderator)
    response = client.post(
        reverse("posts:report_decide", args=[report.public_id]),
        {"decision": "resolve_hide", "note": "x"},
    )
    assert response.status_code == 302  # error toast back to the queue
    report.refresh_from_db()
    assert report.status == ContentReport.Status.OPEN


def test_decide_document_report_access(
    client, make_user, make_community, add_member, make_document, owner, member
):
    secret = make_community("Secret guild", access_mode=INVITE)
    add_member(secret, owner, CommunityRole.CONTRIBUTOR)
    document = make_document(secret, owner)
    reader = make_user("reader@example.com")
    add_member(secret, reader)
    report = _report(document, reader)
    url = reverse("posts:report_decide", args=[report.public_id])
    assert client.post(url, {"decision": "dismiss"}).status_code == 302  # login
    client.force_login(member)  # not a member of the secret community
    assert client.post(url, {"decision": "dismiss"}).status_code == 404
    client.force_login(reader)
    assert client.post(url, {"decision": "dismiss"}).status_code == 403
    client.force_login(owner)
    assert client.post(url, {"decision": "dismiss"}).status_code == 403
    report.refresh_from_db()
    assert report.status == ContentReport.Status.OPEN


def test_queue_query_ceiling_with_documents(
    client, moderator, community, owner, make_document, make_user, django_assert_max_num_queries
):
    reporters = [make_user(f"r{index}@example.com") for index in range(2)]
    for index in range(12):
        document = make_document(community, owner, title=f"D{index}")
        for reporter in reporters:
            _report(document, reporter)
    client.force_login(moderator)
    with django_assert_max_num_queries(17):
        response = client.get(_queue(community))
    assert response.status_code == 200
    assert len(response.context["page_obj"].object_list) == 12
