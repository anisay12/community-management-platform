"""HTTP tests of the private download and preview of document versions (spec § 8, ADR-0001):
permission matrix, download log and counter, response headers."""

import hashlib
import uuid

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.urls import reverse

from communities.models import Community, CommunityMembership
from documents.models import Document, DocumentVersion, DownloadLog

Role = CommunityMembership.Role
Scan = DocumentVersion.ScanStatus

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _production_path(settings):
    """Answer X-Accel-Redirect, as behind Nginx; the DEBUG fallback has its own tests."""
    settings.DEBUG = False


@pytest.fixture
def community(make_community):
    return make_community("Open guild", access_mode=Community.AccessMode.OPEN)


@pytest.fixture
def private_community(make_community):
    return make_community("Private guild", access_mode=Community.AccessMode.INVITE)


@pytest.fixture
def owner(make_user, community, private_community, add_member):
    user = make_user("owner@example.com", first_name="Olga", last_name="Owner")
    add_member(community, user, Role.CONTRIBUTOR)
    add_member(private_community, user, Role.CONTRIBUTOR)
    return user


@pytest.fixture
def member(make_user, community, private_community, add_member):
    user = make_user("member@example.com", first_name="Mia", last_name="Member")
    add_member(community, user)
    add_member(private_community, user)
    return user


@pytest.fixture
def outsider(make_user):
    return make_user("outsider@example.com", first_name="Otto", last_name="Outsider")


@pytest.fixture
def login(client):
    def _login(user):
        client.force_login(user)
        return client

    return _login


def add_version(document, number, *, scan_status=Scan.CLEAN, content=b"%PDF-1.4 old\n", **extra):
    prefix = "documents" if scan_status == Scan.CLEAN else "quarantine"
    key = default_storage.save(f"{prefix}/{uuid.uuid4().hex}", ContentFile(content))
    if scan_status == Scan.INFECTED:
        default_storage.delete(key)
        key = ""
    extra.setdefault("original_filename", f"guide-v{number}.pdf")
    extra.setdefault("mime_type", "application/pdf")
    return DocumentVersion.objects.create(
        document=document,
        number=number,
        version_label=str(number),
        storage_key=key,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        scan_status=scan_status,
        uploaded_by=document.owner,
        **extra,
    )


def download_url(document, number=None):
    if number is None:
        return reverse("documents:download", args=[document.public_id])
    return reverse("documents:download_version", args=[document.public_id, number])


def preview_url(document, number=None):
    if number is None:
        return reverse("documents:preview", args=[document.public_id])
    return reverse("documents:preview_version", args=[document.public_id, number])


# Permission matrix -------------------------------------------------------------------


def test_anonymous_is_sent_to_login(client, community, owner, make_document):
    document = make_document(community, owner)
    response = client.get(download_url(document))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:login"))
    assert not DownloadLog.objects.exists()


def test_outsider_of_a_private_community_gets_404(
    login, private_community, owner, outsider, make_document
):
    document = make_document(private_community, owner)
    assert login(outsider).get(download_url(document)).status_code == 404
    assert login(outsider).get(preview_url(document)).status_code == 404


def test_non_member_of_an_open_community_gets_403(login, community, owner, outsider, make_document):
    document = make_document(community, owner)  # download_min_role defaults to member
    assert login(outsider).get(download_url(document)).status_code == 403
    assert login(outsider).get(preview_url(document)).status_code == 403
    assert not DownloadLog.objects.exists()


def test_member_downloads_the_reference_version(login, community, owner, member, make_document):
    document = make_document(community, owner, filename="guide.pdf")
    response = login(member).get(download_url(document))
    assert response.status_code == 200
    assert response.content == b""
    assert response["X-Accel-Redirect"] == "/_protected/" + document.current_version.storage_key
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == (
        "attachment; filename=\"guide.pdf\"; filename*=UTF-8''guide.pdf"
    )
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "private, no-store"
    assert "Content-Security-Policy" not in response


def test_restricted_document_below_min_role_gets_404(
    login, community, owner, member, make_document
):
    document = make_document(
        community, owner, visibility=Document.Visibility.RESTRICTED, min_role=Role.EXPERT
    )
    assert login(member).get(download_url(document)).status_code == 404


def test_download_min_role_above_the_role_gets_403(login, community, owner, member, make_document):
    document = make_document(community, owner, download_min_role=Role.EXPERT)
    assert login(member).get(download_url(document)).status_code == 403
    # The owner manages the document: always allowed.
    assert login(owner).get(download_url(document)).status_code == 200


def test_pending_reference_is_hidden_from_readers(login, community, owner, member, make_document):
    document = make_document(community, owner, scan_status=Scan.PENDING)
    assert login(member).get(download_url(document)).status_code == 404


@pytest.mark.parametrize("scan_status", [Scan.PENDING, Scan.ERROR])
def test_manager_cannot_download_an_unscanned_version(
    login, community, owner, make_document, scan_status
):
    document = make_document(community, owner, scan_status=scan_status)
    response = login(owner).get(download_url(document))
    assert response.status_code == 403
    assert "X-Accel-Redirect" not in response
    assert "virus scan" in response.content.decode()
    assert not DownloadLog.objects.exists()


def test_infected_version_is_404_for_everyone(
    login, community, owner, member, make_user, add_member, make_document
):
    moderator = make_user("moderator@example.com")
    add_member(community, moderator, Role.MODERATOR)
    document = make_document(community, owner)
    add_version(document, 2, scan_status=Scan.INFECTED)
    for user in (member, owner, moderator):
        assert login(user).get(download_url(document, 2)).status_code == 404
    # Even when the infected object still has its key (deletion pending).
    infected = make_document(community, owner, scan_status=Scan.INFECTED)
    assert login(owner).get(download_url(infected)).status_code == 404


def test_older_clean_version_by_number(login, community, owner, member, make_document):
    document = make_document(community, owner)
    old = add_version(document, 2, original_filename="old.pdf")
    response = login(member).get(download_url(document, 2))
    assert response.status_code == 200
    assert response["X-Accel-Redirect"] == "/_protected/" + old.storage_key
    assert 'filename="old.pdf"' in response["Content-Disposition"]
    log = DownloadLog.objects.get()
    assert log.version == old


def test_older_pending_version_is_hidden_from_readers(
    login, community, owner, member, make_document
):
    document = make_document(community, owner)
    add_version(document, 2, scan_status=Scan.PENDING)
    assert login(member).get(download_url(document, 2)).status_code == 404
    assert login(owner).get(download_url(document, 2)).status_code == 403


def test_unknown_version_number_gets_404(login, community, owner, member, make_document):
    document = make_document(community, owner)
    assert login(member).get(download_url(document, 7)).status_code == 404
    assert login(member).get(preview_url(document, 7)).status_code == 404


def test_version_of_another_document_is_not_reachable(
    login, community, owner, member, make_document
):
    document = make_document(community, owner)
    other = make_document(community, owner)
    add_version(other, 2)
    assert login(member).get(download_url(document, 2)).status_code == 404


def test_member_removed_from_a_private_community_loses_access(
    login, private_community, owner, member, make_document
):
    document = make_document(private_community, owner)
    client = login(member)
    assert client.get(download_url(document)).status_code == 200
    CommunityMembership.objects.filter(community=private_community, user=member).delete()
    assert client.get(download_url(document)).status_code == 404


def test_only_get_is_allowed(login, community, owner, member, make_document):
    document = make_document(community, owner)
    client = login(member)
    assert client.post(download_url(document)).status_code == 405
    assert client.head(download_url(document)).status_code == 405
    assert not DownloadLog.objects.exists()


def test_unverified_privileged_user_is_sent_to_mfa(
    client, community, owner, functional_admin, make_document
):
    document = make_document(community, owner)
    client.force_login(functional_admin)
    response = client.get(download_url(document))
    assert response.status_code == 302
    assert "/mfa/" in response["Location"]


def test_verified_functional_admin_downloads_open_content(
    client, community, owner, functional_admin, verified_login, make_document
):
    document = make_document(community, owner)
    verified_login(client, functional_admin)
    assert client.get(download_url(document)).status_code == 200


# Download log and counter ----------------------------------------------------------


def test_download_is_logged_and_counted(
    login, community, owner, member, make_document, django_capture_on_commit_callbacks
):
    document = make_document(community, owner)
    client = login(member)
    with django_capture_on_commit_callbacks(execute=True):
        assert client.get(download_url(document)).status_code == 200
    log = DownloadLog.objects.get()
    assert (log.user, log.document, log.version) == (member, document, document.current_version)
    assert log.is_preview is False
    document.refresh_from_db()
    assert document.download_count == 1


def test_preview_is_logged_but_not_counted(
    login, community, owner, member, make_document, django_capture_on_commit_callbacks
):
    document = make_document(community, owner)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        assert login(member).get(preview_url(document)).status_code == 200
    assert callbacks == []
    assert DownloadLog.objects.get().is_preview is True
    document.refresh_from_db()
    assert document.download_count == 0


# Preview ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mime_type,filename",
    [
        ("application/pdf", "guide.pdf"),
        ("image/png", "diagram.png"),
        ("image/jpeg", "photo.jpg"),
        ("image/gif", "anim.gif"),
        ("image/webp", "picture.webp"),
    ],
)
def test_preview_headers(login, community, owner, member, make_document, mime_type, filename):
    document = make_document(community, owner, mime_type=mime_type, filename=filename)
    response = login(member).get(preview_url(document))
    assert response.status_code == 200
    assert response.content == b""
    assert response["Content-Type"] == mime_type
    assert response["Content-Disposition"] == (
        f"inline; filename=\"{filename}\"; filename*=UTF-8''{filename}"
    )
    assert response["Content-Security-Policy"] == "sandbox; default-src 'none'"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "private, no-store"


def test_preview_of_an_older_version(login, community, owner, member, make_document):
    document = make_document(community, owner)
    add_version(document, 2)
    response = login(member).get(preview_url(document, 2))
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("inline;")


@pytest.mark.parametrize(
    "mime_type,filename",
    [
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "report.docx",
        ),
        ("text/html", "page.html"),
        ("image/svg+xml", "logo.svg"),
    ],
)
def test_preview_is_refused_for_other_types(
    login, community, owner, member, make_document, mime_type, filename
):
    document = make_document(community, owner, mime_type=mime_type, filename=filename)
    client = login(member)
    assert client.get(preview_url(document)).status_code == 404
    assert not DownloadLog.objects.exists()
    response = client.get(download_url(document))
    assert response.status_code == 200
    assert response["Content-Type"] == mime_type
    assert response["Content-Disposition"].startswith("attachment;")


# Content-Disposition ---------------------------------------------------------------


def test_non_ascii_filename_is_encoded(login, community, owner, member, make_document):
    document = make_document(community, owner, filename="Résumé été 2026 — 東京.pdf")
    response = login(member).get(download_url(document))
    assert response["Content-Disposition"] == (
        'attachment; filename="Resume ete 2026  .pdf"; '
        "filename*=UTF-8''R%C3%A9sum%C3%A9%20%C3%A9t%C3%A9%202026%20%E2%80%94%20"
        "%E6%9D%B1%E4%BA%AC.pdf"
    )


def test_quotes_and_backslashes_cannot_break_the_header(
    login, community, owner, member, make_document
):
    document = make_document(community, owner, filename='a "quoted"\\name;.pdf')
    response = login(member).get(download_url(document))
    assert response["Content-Disposition"] == (
        'attachment; filename="a _quoted__name;.pdf"; '
        "filename*=UTF-8''a%20%22quoted%22%5Cname%3B.pdf"
    )


def test_filename_without_ascii_characters_gets_a_fallback(
    login, community, owner, member, make_document
):
    document = make_document(community, owner, filename="東京")
    response = login(member).get(download_url(document))
    assert response["Content-Disposition"] == (
        "attachment; filename=\"download\"; filename*=UTF-8''%E6%9D%B1%E4%BA%AC"
    )
