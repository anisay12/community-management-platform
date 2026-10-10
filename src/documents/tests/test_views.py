"""Document pages (L5): Resources tab, upload, document page and its actions, post page."""

import re
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.roles import Role
from communities.models import Community, CommunityMembership
from core.tests.helpers import assert_single_h1
from documents import scanner
from documents.models import Document, DocumentLink, DocumentVersion
from documents.scanner import ScanResult
from posts.models import Bookmark, Post
from posts.rendering import render_body

from .samples import PDF, upload

pytestmark = pytest.mark.django_db

CommunityRole = CommunityMembership.Role
PENDING = DocumentVersion.ScanStatus.PENDING


@pytest.fixture(autouse=True)
def _clean_scanner(monkeypatch):
    monkeypatch.setattr(scanner, "scan_stream", lambda stream: ScanResult(clean=True))
    cache.clear()


@pytest.fixture
def world(make_user, make_community, add_member):
    community = make_community("Data guild", access_mode=Community.AccessMode.REQUEST)
    users = {}
    for role in ("member", "contributor", "moderator"):
        users[role] = make_user(f"{role}@example.com", first_name=role.title(), last_name="X")
        add_member(community, users[role], CommunityRole(role))
    users["outsider"] = make_user("outsider@example.com", first_name="Out", last_name="Sider")
    return {"community": community, "users": users}


def _client(user):
    client = Client()
    client.force_login(user)
    return client


def _post(community, author, title="A post"):
    return Post.objects.create(
        community=community,
        author=author,
        title=title,
        body="Body",
        body_html=render_body("Body"),
        status=Post.Status.PUBLISHED,
        published_at=timezone.now(),
        author_display=author.get_full_name(),
    )


def _resources(community):
    return reverse("documents:community_documents", args=[community.slug])


def _detail(document):
    return reverse("documents:detail", args=[document.public_id])


def _upload_data(**extra):
    data = {
        "file": upload(),
        "title": "Python style guide",
        "description": "Line one\nLine two <b>bold</b>",
        "doc_type": Document.DocType.GUIDE,
        "tags": "",
        "visibility": Document.Visibility.COMMUNITY,
        "min_role": CommunityRole.MEMBER,
        "download_min_role": CommunityRole.MEMBER,
        "expires_at": "",
        "review_due_at": "",
        "version_label": "",
        "change_note": "First version",
        "idempotency_key": str(uuid.uuid4()),
    }
    data.update(extra)
    return data


# --- permission matrix --------------------------------------------------------------------


@pytest.fixture
def routes(world, make_document):
    community = world["community"]
    contributor = world["users"]["contributor"]
    document = make_document(community, contributor, title="Shared guide")
    post = _post(community, contributor)
    link = DocumentLink.objects.create(document=document, post=post, created_by=contributor)
    return {
        "resources": ("get", _resources(community)),
        "create": ("get", reverse("documents:create", args=[community.slug])),
        "detail": ("get", _detail(document)),
        "edit": ("get", reverse("documents:edit", args=[document.public_id])),
        "add_version": ("get", reverse("documents:add_version", args=[document.public_id])),
        "archive_confirm": ("get", reverse("documents:archive", args=[document.public_id])),
        "set_reference": (
            "post",
            reverse("documents:set_reference", args=[document.public_id, 1]),
        ),
        "link": ("post", reverse("documents:link", args=[document.public_id])),
        "unlink": ("post", reverse("documents:unlink", args=[document.public_id, link.pk])),
    }


# viewer -> expected status per route
MATRIX = {
    # The on-request community is listed (its metadata is visible): uploading there is a 403;
    # its content (Resources tab, documents) stays hidden (404), as the Feed tab.
    "outsider": {name: 404 for name in ("resources", "detail", "edit", "add_version")}
    | {"create": 403, "archive_confirm": 404, "set_reference": 404, "link": 404, "unlink": 404},
    "member": {
        "resources": 200,
        "create": 403,
        "detail": 200,
        "edit": 403,
        "add_version": 403,
        "archive_confirm": 403,
        "set_reference": 403,
        "link": 403,
        "unlink": 403,
    },
    "contributor": {  # the owner
        "resources": 200,
        "create": 200,
        "detail": 200,
        "edit": 200,
        "add_version": 200,
        "archive_confirm": 200,
        "set_reference": 302,
        "link": 302,
        "unlink": 302,
    },
    "moderator": {
        "resources": 200,
        "create": 200,
        "detail": 200,
        "edit": 200,
        "add_version": 200,
        "archive_confirm": 200,
        "set_reference": 302,
        "link": 302,
        "unlink": 302,
    },
}


@pytest.mark.parametrize("viewer", list(MATRIX))
def test_permission_matrix(world, routes, viewer):
    client = _client(world["users"][viewer])
    statuses = {
        name: getattr(client, method)(url).status_code for name, (method, url) in routes.items()
    }
    assert statuses == MATRIX[viewer]


def test_anonymous_is_redirected_to_login(client, routes):
    for _name, (method, url) in routes.items():
        response = getattr(client, method)(url)
        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]


def test_writes_refuse_get(world, routes):
    client = _client(world["users"]["moderator"])
    for name in ("set_reference", "link", "unlink"):
        assert client.get(routes[name][1]).status_code == 405


def test_content_closed_open_community_is_403(make_user, make_community, verified_login):
    """A technical administrator knows an open community exists but does not read it."""
    community = make_community("Open guild")
    technical = make_user("tech@example.com")
    technical.groups.add(Group.objects.get(name=Role.TECHNICAL_ADMIN))
    client = Client()
    verified_login(client, technical)
    assert client.get(_resources(community)).status_code == 403


# --- Resources tab ------------------------------------------------------------------------


def test_tab_visible_to_readers_not_to_outsiders(world):
    community = world["community"]
    about = reverse("communities:detail", args=[community.slug])
    member_html = _client(world["users"]["member"]).get(about).content.decode()
    assert _resources(community) in member_html
    # The outsider cannot see the private community at all; an open community shows the tab.
    response = _client(world["users"]["outsider"]).get(about)
    assert response.status_code == 404 or _resources(community) not in response.content.decode()


def test_tab_listed_for_open_community_outsider(make_user, make_community):
    community = make_community("Open guild")
    html = (
        _client(make_user("x@example.com"))
        .get(reverse("communities:detail", args=[community.slug]))
        .content.decode()
    )
    assert _resources(community) in html


def test_resources_list_badges_and_single_h1(world, make_document):
    community = world["community"]
    owner = world["users"]["contributor"]
    make_document(
        community,
        owner,
        title="Restricted guide",
        visibility=Document.Visibility.RESTRICTED,
        min_role=CommunityRole.MEMBER,
    )
    response = _client(world["users"]["member"]).get(_resources(community))
    assert response.status_code == 200
    assert_single_h1(response)
    html = response.content.decode()
    assert "Restricted guide" in html
    assert "Restricted" in html
    assert "Upload a document" not in html  # members may not upload here


def test_empty_state_with_upload_action(world):
    html = (
        _client(world["users"]["contributor"]).get(_resources(world["community"])).content.decode()
    )
    assert "tl-empty-state" in html
    assert reverse("documents:create", args=[world["community"].slug]) in html


def test_managers_see_pending_and_archived_readers_do_not(world, make_document):
    community = world["community"]
    owner = world["users"]["contributor"]
    make_document(community, owner, title="Pending file", scan_status=PENDING)
    make_document(community, owner, title="Old file", status=Document.Status.ARCHIVED)
    make_document(community, owner, title="Live file")
    member = _client(world["users"]["member"])
    moderator = _client(world["users"]["moderator"])
    default = moderator.get(_resources(community)).content.decode()
    assert "Live file" in default and "Pending file" not in default
    scanning = moderator.get(_resources(community) + "?status=scanning").content.decode()
    assert "Pending file" in scanning and "Scan in progress" in scanning
    archived = moderator.get(_resources(community) + "?status=archived").content.decode()
    assert "Old file" in archived and "Live file" not in archived
    for query in ("", "?status=scanning", "?status=archived", "?status=all"):
        html = member.get(_resources(community) + query).content.decode()
        assert "Pending file" not in html and "Old file" not in html
        assert 'name="status"' not in html


def test_filters_sort_and_pagination(world, make_document):
    community = world["community"]
    owner = world["users"]["contributor"]
    make_document(community, owner, title="Alpha template", doc_type="template", download_count=1)
    make_document(community, owner, title="Beta guide", doc_type="guide", download_count=9)
    client = _client(world["users"]["member"])
    url = _resources(community)
    html = client.get(url + "?doc_type=template").content.decode()
    assert "Alpha template" in html and "Beta guide" not in html
    html = client.get(url + "?q=beta").content.decode()
    assert "Beta guide" in html and "Alpha template" not in html
    html = client.get(url + "?sort=downloads").content.decode()
    assert html.index("Beta guide") < html.index("Alpha template")
    html = client.get(url + "?sort=title").content.decode()
    assert html.index("Alpha template") < html.index("Beta guide")
    html = client.get(url + "?q=nothing-matches").content.decode()
    assert "No document matches these filters" in html
    for index in range(22):
        make_document(community, owner, title=f"Bulk {index:02d}")
    response = client.get(url)
    assert len(response.context["page_obj"].object_list) == 20
    assert 'rel="next"' in response.content.decode()
    assert len(client.get(url + "?per_page=500").context["page_obj"].object_list) == 24


def test_tag_filter(world, make_document):
    from taxonomy.models import Tag

    community = world["community"]
    owner = world["users"]["contributor"]
    tag = Tag.objects.create(name="Python", slug="python")
    tagged = make_document(community, owner, title="Tagged doc")
    tagged.tags.add(tag)
    make_document(community, owner, title="Plain doc")
    html = (
        _client(world["users"]["member"])
        .get(_resources(community) + f"?tag={tag.slug}")
        .content.decode()
    )
    assert "Tagged doc" in html and "Plain doc" not in html


# --- upload -------------------------------------------------------------------------------


def test_upload_form_renders(world):
    response = _client(world["users"]["contributor"]).get(
        reverse("documents:create", args=[world["community"].slug])
    )
    html = response.content.decode()
    assert_single_h1(response)
    assert 'enctype="multipart/form-data"' in html
    assert 'accept=".csv' in html or "accept=" in html
    assert 'name="idempotency_key"' in html
    assert "aria-describedby" in html


def test_upload_creates_document_and_scans(world, django_capture_on_commit_callbacks):
    community = world["community"]
    client = _client(world["users"]["contributor"])
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("documents:create", args=[community.slug]), _upload_data())
    document = Document.objects.get()
    assert response.status_code == 302
    assert response["Location"] == _detail(document)
    assert document.current_version.scan_status == DocumentVersion.ScanStatus.CLEAN
    page = client.get(_detail(document))
    assert "being scanned" in page.content.decode()
    assert "Line one<br>Line two &lt;b&gt;bold&lt;/b&gt;" in page.content.decode()


def test_idempotent_double_submit_creates_one_document(world):
    community = world["community"]
    client = _client(world["users"]["contributor"])
    key = str(uuid.uuid4())
    url = reverse("documents:create", args=[community.slug])
    first = client.post(url, _upload_data(idempotency_key=key))
    second = client.post(url, _upload_data(idempotency_key=key))
    assert Document.objects.count() == 1
    assert first["Location"] == second["Location"] == _detail(Document.objects.get())
    client.post(url, _upload_data())
    assert Document.objects.count() == 2


def test_upload_refused_file_is_a_form_error(world):
    client = _client(world["users"]["contributor"])
    response = client.post(
        reverse("documents:create", args=[world["community"].slug]),
        _upload_data(file=upload("evil.exe", b"MZ\x90\x00")),
    )
    assert response.status_code == 200
    assert Document.objects.count() == 0
    assert response.context["form"].errors["file"]


def test_upload_expiry_in_user_timezone(world):
    client = _client(world["users"]["contributor"])
    day = (timezone.localdate() + timedelta(days=10)).isoformat()
    client.post(
        reverse("documents:create", args=[world["community"].slug]),
        _upload_data(expires_at=day),
    )
    document = Document.objects.get()
    assert timezone.localtime(document.expires_at).date().isoformat() == day


# --- document page ------------------------------------------------------------------------


def test_detail_reader(world, make_document):
    document = make_document(world["community"], world["users"]["contributor"], title="Guide")
    response = _client(world["users"]["member"]).get(_detail(document))
    assert response.status_code == 200
    assert_single_h1(response)
    html = response.content.decode()
    assert reverse("documents:download", args=[document.public_id]) in html
    assert reverse("documents:preview", args=[document.public_id]) in html
    assert 'target="_blank"' in html
    assert "<iframe" not in html
    assert reverse("posts:bookmark_document", args=[document.public_id]) in html
    assert reverse("posts:report_document", args=[document.public_id]) in html
    assert reverse("documents:edit", args=[document.public_id]) not in html


def test_detail_bookmarked_state(world, make_document):
    member = world["users"]["member"]
    document = make_document(world["community"], world["users"]["contributor"])
    Bookmark.objects.create(user=member, document=document)
    html = _client(member).get(_detail(document)).content.decode()
    assert 'aria-pressed="true"' in html


def test_detail_pending_for_manager_has_no_download(world, make_document):
    document = make_document(world["community"], world["users"]["contributor"], scan_status=PENDING)
    assert _client(world["users"]["member"]).get(_detail(document)).status_code == 404
    html = _client(world["users"]["moderator"]).get(_detail(document)).content.decode()
    assert "Scan in progress" in html
    assert reverse("documents:download", args=[document.public_id]) not in html


def test_set_reference_archive_restore(world, make_document):
    community = world["community"]
    owner = world["users"]["contributor"]
    document = make_document(community, owner)
    first = document.current_version
    first.is_reference = False
    first.save()
    second = DocumentVersion.objects.create(
        document=document,
        number=2,
        version_label="2",
        storage_key="documents/abc",
        original_filename="g.pdf",
        mime_type="application/pdf",
        size=3,
        sha256="0" * 64,
        scan_status=DocumentVersion.ScanStatus.CLEAN,
        is_reference=True,
    )
    document.current_version = second
    document.save()
    client = _client(owner)
    client.post(reverse("documents:set_reference", args=[document.public_id, 1]))
    document.refresh_from_db()
    assert document.current_version_id == first.pk
    client.post(reverse("documents:archive", args=[document.public_id]))
    document.refresh_from_db()
    assert document.status == Document.Status.ARCHIVED
    html = client.get(_detail(document)).content.decode()
    assert reverse("documents:restore", args=[document.public_id]) in html
    client.post(reverse("documents:restore", args=[document.public_id]))
    document.refresh_from_db()
    assert document.status == Document.Status.ACTIVE


def test_archive_twice_is_a_toast(world, make_document):
    document = make_document(
        world["community"], world["users"]["contributor"], status=Document.Status.ARCHIVED
    )
    response = _client(world["users"]["moderator"]).post(
        reverse("documents:archive", args=[document.public_id]), follow=True
    )
    assert response.status_code == 200
    assert "not possible" in response.content.decode()


def test_edit_metadata(world, make_document):
    document = make_document(world["community"], world["users"]["contributor"], title="Old")
    client = _client(world["users"]["contributor"])
    url = reverse("documents:edit", args=[document.public_id])
    assert 'value="Old"' in client.get(url).content.decode()
    data = _upload_data(title="New title")
    del data["file"], data["idempotency_key"], data["change_note"], data["version_label"]
    response = client.post(url, data)
    assert response.status_code == 302
    document.refresh_from_db()
    assert document.title == "New title"


def test_add_version_idempotent(world, make_document):
    document = make_document(world["community"], world["users"]["contributor"])
    client = _client(world["users"]["contributor"])
    url = reverse("documents:add_version", args=[document.public_id])
    html = client.get(url).content.decode()
    assert re.search(r'name="make_reference"[^>]*checked', html)
    key = str(uuid.uuid4())
    for _ in range(2):
        response = client.post(
            url,
            {
                "file": upload("v2.pdf", PDF),
                "idempotency_key": key,
                "make_reference": "on",
                "change_note": "Update",
                "version_label": "",
            },
        )
        assert response.status_code == 302
    assert document.versions.count() == 2


def test_link_and_unlink_post(world, make_document):
    community = world["community"]
    owner = world["users"]["contributor"]
    document = make_document(community, owner)
    post = _post(community, owner, title="Release notes")
    client = _client(owner)
    html = client.get(_detail(document)).content.decode()
    assert "Release notes" in html  # offered in the select
    client.post(reverse("documents:link", args=[document.public_id]), {"post": post.public_id})
    link = DocumentLink.objects.get()
    assert link.post == post
    # The post page lists the linked document for a reader.
    post_html = (
        _client(world["users"]["member"])
        .get(reverse("posts:detail", args=[community.slug, post.public_id]))
        .content.decode()
    )
    assert "Linked documents" in post_html and _detail(document) in post_html
    client.post(reverse("documents:unlink", args=[document.public_id, link.pk]))
    assert not DocumentLink.objects.exists()


def test_post_page_hides_documents_reader_cannot_open(world, make_document):
    community = world["community"]
    owner = world["users"]["contributor"]
    document = make_document(community, owner, scan_status=PENDING)
    post = _post(community, owner)
    DocumentLink.objects.create(document=document, post=post)
    html = (
        _client(world["users"]["member"])
        .get(reverse("posts:detail", args=[community.slug, post.public_id]))
        .content.decode()
    )
    assert "Linked documents" not in html


def test_upload_to_hidden_community_is_404(make_user, make_community):
    secret = make_community("Secret guild", access_mode=Community.AccessMode.INVITE)
    client = _client(make_user("x@example.com"))
    assert client.get(reverse("documents:create", args=[secret.slug])).status_code == 404
