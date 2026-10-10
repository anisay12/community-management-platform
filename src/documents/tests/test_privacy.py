"""GDPR hooks of documents: export section and anonymization (``documents.privacy``)."""

import json

import pytest

from accounts import privacy as account_privacy
from accounts import services as account_services
from documents.models import Document, DocumentVersion, DownloadLog
from documents.privacy import anonymize_documents, export_documents

pytestmark = pytest.mark.django_db


@pytest.fixture
def community(make_community):
    return make_community()


@pytest.fixture
def owner(make_user, community, add_member):
    user = make_user("owner@example.com", first_name="Olga", last_name="Owner")
    add_member(community, user)
    return user


@pytest.fixture
def other(make_user, community, add_member):
    user = make_user("other@example.com", first_name="Otto", last_name="Other")
    add_member(community, user)
    return user


def _add_version(document, user, number, filename="v2.pdf", size=42):
    return DocumentVersion.objects.create(
        document=document,
        number=number,
        version_label=str(number),
        storage_key=f"documents/key-{document.pk}-{number}",
        original_filename=filename,
        mime_type="application/pdf",
        size=size,
        sha256="0" * 64,
        scan_status=DocumentVersion.ScanStatus.CLEAN,
        uploaded_by=user,
    )


def test_hooks_are_registered():
    assert account_privacy._exporters["documents"] is export_documents
    assert anonymize_documents in account_privacy._anonymizers


def test_export_lists_documents_versions_and_downloads(make_document, community, owner, other):
    mine = make_document(community, owner, title="My guide", filename="guide.pdf")
    _add_version(mine, owner, 2, filename="guide-v2.pdf", size=1234)
    theirs = make_document(community, other, title="Their template")
    _add_version(theirs, owner, 2, filename="fix.docx", size=99)
    DownloadLog.objects.create(document=theirs, version=theirs.current_version, user=owner)
    DownloadLog.objects.create(document=mine, version=mine.current_version, user=other)

    data = export_documents(owner)

    (owned,) = data["owned"]
    assert owned["title"] == "My guide"
    assert owned["community"] == community.name
    assert owned["created_at"] == mine.created_at
    assert [(v["filename"], v["size"]) for v in owned["versions"]] == [
        ("guide.pdf", mine.current_version.size),
        ("guide-v2.pdf", 1234),
    ]
    (uploaded,) = data["versions_uploaded_to_other_documents"]
    assert uploaded["document"] == "Their template"
    assert (uploaded["filename"], uploaded["size"]) == ("fix.docx", 99)
    (download,) = data["downloads"]
    assert download["document"] == "Their template" and download["preview"] is False
    # The section is part of the JSON export of the account.
    rendered = json.loads(account_privacy.render_export(owner))
    assert rendered["sections"]["documents"]["owned"][0]["title"] == "My guide"


def test_export_contains_nothing_of_other_users(make_document, community, owner, other):
    document = make_document(community, other)
    DownloadLog.objects.create(document=document, version=document.current_version, user=other)
    assert export_documents(owner) == {
        "owned": [],
        "versions_uploaded_to_other_documents": [],
        "downloads": [],
    }


def test_anonymizer_deletes_downloads_and_renames_owner(make_document, community, owner, other):
    mine = make_document(community, owner)
    theirs = make_document(community, other)
    DownloadLog.objects.create(document=theirs, version=theirs.current_version, user=owner)
    kept = DownloadLog.objects.create(document=mine, version=mine.current_version, user=other)

    anonymize_documents(owner)

    assert list(DownloadLog.objects.all()) == [kept]
    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.owner_display == "Former employee"
    assert mine.owner == owner and mine.status == Document.Status.ACTIVE  # the document stays
    assert theirs.owner_display == other.get_full_name()


def test_anonymize_user_runs_the_documents_hook(
    make_document, community, owner, functional_admin, django_capture_on_commit_callbacks
):
    document = make_document(community, owner)
    DownloadLog.objects.create(document=document, version=document.current_version, user=owner)
    account_services.deactivate_user(actor=functional_admin, user=owner)
    owner.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        account_privacy.anonymize_user(actor=functional_admin, user=owner)
    document.refresh_from_db()
    assert document.owner_display == "Former employee"
    assert not DownloadLog.objects.exists()
