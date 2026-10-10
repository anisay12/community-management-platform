"""How a download is served (ADR-0001): internal redirect target built from a presigned
storage URL, development streaming fallback, the internal Nginx location, and
``downloads.record_download``."""

import re
from pathlib import Path

import pytest
from django.urls import reverse

from communities.models import Community, CommunityMembership
from core.errors import DomainError
from documents import downloads
from documents.models import DownloadLog

pytestmark = pytest.mark.django_db

NGINX_CONF = Path(__file__).resolve().parents[3] / "docker" / "nginx" / "default.conf"
SIGNATURE = "c0ffee5ecret"
QUERY = (
    "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=key%2F20261010%2Fus-east-1%2Fs3"
    f"%2Faws4_request&X-Amz-Date=20261010T120000Z&X-Amz-Expires=60&X-Amz-Signature={SIGNATURE}"
)


class SigningStorage:
    """Stands for S3Storage(querystring_auth=True): ``url()`` returns a presigned URL."""

    def __init__(self):
        self.calls = []

    def url(self, name, parameters=None, expire=None, http_method=None):
        self.calls.append((name, expire))
        return f"http://s3:8333/talan-documents/{name}?{QUERY}"


@pytest.fixture
def community(make_community):
    return make_community("Open guild", access_mode=Community.AccessMode.OPEN)


@pytest.fixture
def owner(make_user, community, add_member):
    user = make_user("owner@example.com")
    add_member(community, user, CommunityMembership.Role.CONTRIBUTOR)
    return user


@pytest.fixture
def member(make_user, community, add_member):
    user = make_user("member@example.com")
    add_member(community, user)
    return user


@pytest.fixture
def document(community, owner, make_document):
    return make_document(community, owner, content=b"%PDF-1.4 the real bytes\n")


@pytest.fixture
def member_client(client, member):
    client.force_login(member)
    return client


@pytest.fixture
def signing_storage(monkeypatch):
    storage = SigningStorage()
    monkeypatch.setattr(downloads, "default_storage", storage)
    return storage


def _download(document):
    return reverse("documents:download", args=[document.public_id])


def _preview(document):
    return reverse("documents:preview", args=[document.public_id])


# X-Accel-Redirect target -------------------------------------------------------------


def test_presigned_path_and_query_go_in_the_internal_redirect_only(
    settings, member_client, document, signing_storage
):
    settings.DEBUG = False
    key = document.current_version.storage_key
    for url in (_download(document), _preview(document)):
        response = member_client.get(url)
        assert response.status_code == 200
        assert response["X-Accel-Redirect"] == f"/_protected/talan-documents/{key}?{QUERY}"
        assert response.content == b""
        for header, value in response.items():
            if header.lower() != "x-accel-redirect":
                assert SIGNATURE not in value, header
                assert "s3:8333" not in value, header
                assert "X-Amz" not in value, header
        assert SIGNATURE not in str(response.cookies)
    assert signing_storage.calls == [(key, downloads.PRESIGNED_URL_TTL_SECONDS)] * 2


def test_storage_without_signed_urls_falls_back_to_the_storage_key(document):
    # The in-memory test storage has no ``expire`` argument and relative URLs.
    version = document.current_version
    assert downloads.protected_location(version) == "/_protected/" + version.storage_key


def test_relative_url_from_storage_falls_back_to_the_storage_key(monkeypatch, document):
    class LocalStorage:
        def url(self, name, expire=None):
            return f"/media/{name}"

    monkeypatch.setattr(downloads, "default_storage", LocalStorage())
    version = document.current_version
    assert downloads.protected_location(version) == "/_protected/" + version.storage_key


def test_presigned_url_without_query_keeps_the_path(monkeypatch, document):
    class PublicStorage:
        def url(self, name, expire=None):
            return f"https://bucket.example.com/{name}"

    monkeypatch.setattr(downloads, "default_storage", PublicStorage())
    version = document.current_version
    assert downloads.protected_location(version) == "/_protected/" + version.storage_key


def test_protected_prefix_is_not_routed_by_django(member_client, document):
    key = document.current_version.storage_key
    assert member_client.get("/_protected/" + key).status_code == 404
    assert member_client.get("/_protected/").status_code == 404


# Development fallback --------------------------------------------------------------


def test_debug_streams_the_file_with_the_same_headers(settings, member_client, document):
    settings.DEBUG = True
    response = member_client.get(_download(document))
    assert response.status_code == 200
    assert response.streaming
    assert b"".join(response.streaming_content) == b"%PDF-1.4 the real bytes\n"
    assert "X-Accel-Redirect" not in response
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith('attachment; filename="guide.pdf"')
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "private, no-store"
    assert DownloadLog.objects.count() == 1


def test_debug_preview_keeps_the_sandbox(settings, member_client, document):
    settings.DEBUG = True
    response = member_client.get(_preview(document))
    assert b"".join(response.streaming_content) == b"%PDF-1.4 the real bytes\n"
    assert response["Content-Disposition"].startswith("inline;")
    assert response["Content-Security-Policy"] == "sandbox; default-src 'none'"


def test_debug_streaming_can_be_turned_off(settings, member_client, document):
    settings.DEBUG = True
    settings.DOCUMENT_DEV_STREAMING = False
    response = member_client.get(_download(document))
    assert response["X-Accel-Redirect"].startswith("/_protected/")
    assert response.content == b""


def test_no_streaming_without_debug(settings, member_client, document):
    settings.DEBUG = False
    settings.DOCUMENT_DEV_STREAMING = True
    response = member_client.get(_download(document))
    assert not response.streaming
    assert response.content == b""
    assert "X-Accel-Redirect" in response


# Service ---------------------------------------------------------------------------


def test_record_download_refuses_a_forbidden_download(make_user, document):
    outsider = make_user("outsider@example.com")
    with pytest.raises(DomainError) as error:
        downloads.record_download(
            user=outsider, document=document, version=document.current_version
        )
    assert error.value.code == "forbidden"
    assert not DownloadLog.objects.exists()


def test_record_download_schedules_the_recount(
    member, document, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        log = downloads.record_download(
            user=member, document=document, version=document.current_version
        )
    assert len(callbacks) == 1
    assert log.is_preview is False
    document.refresh_from_db()
    assert document.download_count == 1


# Nginx -----------------------------------------------------------------------------


def _protected_block(conf: str) -> str:
    match = re.search(r"location /_protected/ \{(.*?)\n    \}", conf, re.S)
    assert match, "docker/nginx/default.conf must declare location /_protected/"
    return match.group(1)


def test_nginx_protected_location_is_internal():
    block = _protected_block(NGINX_CONF.read_text(encoding="utf-8"))
    directives = [line.strip() for line in block.splitlines()]
    assert "internal;" in directives
    assert "proxy_pass ${STORAGE_ORIGIN}/;" in directives
    # The browser's credentials never reach the storage.
    assert "proxy_pass_request_headers off;" in directives
    # Django's headers win over the storage's.
    for header in ("Content-Type", "Content-Disposition", "Cache-Control", "Set-Cookie"):
        assert f"proxy_hide_header {header};" in directives
    assert 'add_header X-Content-Type-Options "nosniff" always;' in directives


def test_nginx_prefix_matches_the_setting(settings):
    assert settings.DOCUMENT_PROTECTED_PREFIX == "/_protected/"
    assert f"location {settings.DOCUMENT_PROTECTED_PREFIX} {{" in NGINX_CONF.read_text(
        encoding="utf-8"
    )
