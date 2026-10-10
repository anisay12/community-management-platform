"""Content to review (``documents.selectors_review.documents_to_review``)."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from communities.models import Community
from communities.roles import CommunityRole
from documents.models import Document
from documents.selectors_review import documents_to_review
from posts.models import ContentReport

pytestmark = pytest.mark.django_db

Reason = ContentReport.Reason


@pytest.fixture
def community(make_community):
    return make_community()


@pytest.fixture
def owner(make_user, community, add_member):
    user = make_user("owner@example.com", first_name="Olga", last_name="Owner")
    add_member(community, user, CommunityRole.CONTRIBUTOR)
    return user


@pytest.fixture
def moderator(make_user, community, add_member):
    user = make_user("mod@example.com", first_name="Mo", last_name="Derator")
    add_member(community, user, CommunityRole.MODERATOR)
    return user


@pytest.fixture
def reader(make_user, community, add_member):
    user = make_user("reader@example.com", first_name="Rita", last_name="Reader")
    add_member(community, user)
    return user


def _report(document, reporter, reason=Reason.OUTDATED, **extra):
    return ContentReport.objects.create(
        reporter=reporter, document=document, community=document.community, reason=reason, **extra
    )


def _past(days=1):
    return timezone.now() - timedelta(days=days)


def test_due_and_outdated_documents_are_listed_in_review_order(
    make_document, community, owner, moderator, reader
):
    older = make_document(community, owner, title="Older", review_due_at=_past(10))
    recent = make_document(community, owner, title="Recent", review_due_at=_past(1))
    reported = make_document(community, owner, title="Reported")
    _report(reported, reader)
    make_document(community, owner, title="Future", review_due_at=timezone.now() + timedelta(1))
    make_document(community, owner, title="Plain")

    assert list(documents_to_review(owner)) == [older, recent, reported]
    assert list(documents_to_review(moderator)) == [older, recent, reported]
    flags = {d.title: d.has_outdated_report for d in documents_to_review(owner)}
    assert flags == {"Older": False, "Recent": False, "Reported": True}


def test_only_open_outdated_reports_count(make_document, community, owner, reader, moderator):
    spam = make_document(community, owner, title="Spam")
    _report(spam, reader, Reason.SPAM)
    handled = make_document(community, owner, title="Handled")
    _report(handled, reader, status=ContentReport.Status.DISMISSED)
    assert list(documents_to_review(owner)) == []


def test_only_active_documents(make_document, community, owner):
    for status in (Document.Status.ARCHIVED, Document.Status.EXPIRED):
        make_document(community, owner, status=status, review_due_at=_past())
    assert list(documents_to_review(owner)) == []


def test_only_documents_the_user_manages(
    make_document, make_user, add_member, community, owner, reader
):
    make_document(community, owner, review_due_at=_past())
    assert list(documents_to_review(reader)) == []
    assert list(documents_to_review(AnonymousUser())) == []
    stranger = make_user("stranger@example.com")
    assert list(documents_to_review(stranger)) == []


def test_functional_admin_needs_content_access(
    make_document, make_community, community, owner, functional_admin, add_member
):
    due = make_document(community, owner, review_due_at=_past())
    assert list(documents_to_review(functional_admin)) == [due]  # open community
    private = make_community("Secret", access_mode=Community.AccessMode.INVITE)
    add_member(private, owner, CommunityRole.CONTRIBUTOR)
    make_document(private, owner, review_due_at=_past())
    assert list(documents_to_review(functional_admin)) == [due]


def test_former_owner_no_longer_sees_documents_of_a_private_community(
    make_document, make_community, make_user, add_member
):
    private = make_community("Secret", access_mode=Community.AccessMode.INVITE)
    user = make_user("leaver@example.com")
    membership = add_member(private, user, CommunityRole.CONTRIBUTOR)
    make_document(private, user, review_due_at=_past())
    assert documents_to_review(user).count() == 1
    membership.delete()
    assert documents_to_review(user).count() == 0


def test_filter_by_community(make_document, make_community, add_member, community, owner):
    other = make_community("Data guild")
    add_member(other, owner, CommunityRole.CONTRIBUTOR)
    here = make_document(community, owner, review_due_at=_past())
    there = make_document(other, owner, review_due_at=_past(2))
    assert list(documents_to_review(owner)) == [there, here]
    assert list(documents_to_review(owner, community=community)) == [here]
