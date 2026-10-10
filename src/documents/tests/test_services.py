from datetime import timedelta

import pytest
from django.contrib.postgres.search import SearchQuery
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.utils import timezone

from audit.models import AuditEvent
from communities.models import Community, CommunityMembership
from core.errors import DomainError
from documents import scanner, services
from documents.models import Document, DocumentLink, DocumentVersion
from documents.scanner import ScanResult
from notifications.models import Notification
from posts.models import Post
from posts.rendering import render_body
from taxonomy.models import Tag

from .samples import PDF, SAMPLES, upload

Role = CommunityMembership.Role


@pytest.fixture(autouse=True)
def _clean_scanner_and_limits(monkeypatch):
    """Scans (run on commit by tests capturing callbacks) find every file clean; rate limit
    counters start empty."""
    monkeypatch.setattr(scanner, "scan_stream", lambda stream: ScanResult(clean=True))
    cache.clear()


@pytest.fixture
def community(make_community):
    return make_community()


@pytest.fixture
def people(make_user, community, add_member):
    users = {}
    for role in ("member", "contributor", "moderator"):
        users[role] = make_user(f"{role}@example.com", first_name=role.title())
        add_member(community, users[role], Role(role))
    users["outsider"] = make_user("outsider@example.com", first_name="Out")
    return users


def _quarantine_keys():
    try:
        return set(default_storage.listdir("quarantine")[1])
    except FileNotFoundError:
        return set()


def _create(actor, community, **extra):
    extra.setdefault("upload", upload())
    extra.setdefault("title", "Python style guide")
    return services.create_document(actor=actor, community=community, **extra)


def _query(text):
    return SearchQuery(text, config="simple")


def _audit(action):
    return AuditEvent.objects.filter(action=action)


def make_post(community, author, **extra):
    extra.setdefault("status", Post.Status.PUBLISHED)
    return Post.objects.create(
        community=community,
        author=author,
        author_display=author.get_full_name(),
        title="A post",
        body="Body",
        body_html=render_body("Body"),
        published_at=timezone.now(),
        **extra,
    )


# --- create_document --------------------------------------------------------------------


def test_contributor_creates_document(people, community, django_capture_on_commit_callbacks):
    actor = people["contributor"]
    with django_capture_on_commit_callbacks() as callbacks:
        document = _create(
            actor,
            community,
            description="How we write Python",
            doc_type=Document.DocType.GUIDE,
            change_note="First draft",
        )
    version = document.current_version
    assert document.owner == actor
    assert document.owner_display == "Contributor Doe"
    assert document.status == Document.Status.ACTIVE
    assert version.number == 1
    assert version.version_label == "1"
    assert version.is_reference
    assert version.scan_status == DocumentVersion.ScanStatus.PENDING
    assert version.storage_key.startswith("quarantine/")
    assert "guide" not in version.storage_key
    assert version.original_filename == "guide.pdf"
    assert version.mime_type == "application/pdf"
    assert version.size == len(PDF)
    assert version.change_note == "First draft"
    with default_storage.open(version.storage_key) as stored:
        assert stored.read() == PDF
    document.refresh_from_db()
    assert document.search_vector is not None
    event = _audit("document.create").get()
    assert event.actor == actor
    assert event.community_id == community.pk
    assert event.target_id == str(document.public_id)
    assert len(callbacks) == 1  # the scan, queued on commit


def test_scan_runs_on_commit(people, community, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        document = _create(people["contributor"], community)
    version = DocumentVersion.objects.get(document=document)
    assert version.scan_status == DocumentVersion.ScanStatus.CLEAN
    assert version.storage_key.startswith("documents/")


def test_search_vector_covers_title_tags_description(people, community):
    Tag.objects.create(name="Kubernetes", key="kubernetes")
    document = _create(
        people["contributor"], community, description="clusters", tags=["Kubernetes"]
    )
    found = Document.objects.filter(search_vector=_query("kubernetes"))
    assert list(found) == [document]
    assert list(Document.objects.filter(search_vector=_query("clusters"))) == [document]


def test_member_without_member_uploads_refused(people, community):
    with pytest.raises(DomainError) as error:
        _create(people["member"], community)
    assert error.value.code == "forbidden"
    assert not Document.objects.exists()


def test_member_uploads_when_community_allows(people, community):
    Community.objects.filter(pk=community.pk).update(allow_member_uploads=True)
    community.refresh_from_db()
    document = _create(people["member"], community)
    assert document.owner == people["member"]


def test_outsider_refused(people, community):
    with pytest.raises(DomainError) as error:
        _create(people["outsider"], community)
    assert error.value.code == "not_member"


@pytest.mark.parametrize("status", [Community.Status.SUSPENDED, Community.Status.ARCHIVED])
def test_read_only_community_refused(people, community, status):
    community.status = status
    community.save(update_fields=["status"])
    with pytest.raises(DomainError) as error:
        _create(people["contributor"], community)
    assert error.value.code == "read_only"


def test_invalid_file_refused_before_storing(people, community):
    before = _quarantine_keys()
    with pytest.raises(DomainError) as error:
        _create(people["contributor"], community, upload=upload("logo.svg", b"<svg/>"))
    assert error.value.code == "extension_refused"
    assert _quarantine_keys() == before


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"title": "  "}, "title_required"),
        ({"title": "x" * 201}, "title_too_long"),
        ({"description": "x" * 5001}, "description_too_long"),
        ({"doc_type": "poem"}, "invalid_choice"),
        ({"visibility": "secret"}, "invalid_choice"),
        ({"min_role": "king"}, "invalid_choice"),
        ({"expires_at": timezone.now() - timedelta(days=1)}, "invalid_date"),
        ({"change_note": "x" * 501}, "change_note_too_long"),
        ({"version_label": "x" * 41}, "version_label_too_long"),
    ],
)
def test_invalid_metadata(people, community, extra, code):
    with pytest.raises(DomainError) as error:
        _create(people["contributor"], community, **extra)
    assert error.value.code == code


def test_new_tag_needs_contributor(people, community):
    Community.objects.filter(pk=community.pk).update(allow_member_uploads=True)
    community.refresh_from_db()
    Tag.objects.create(name="Django", key="django")
    before = _quarantine_keys()
    with pytest.raises(DomainError) as error:
        _create(people["member"], community, tags=["Brand new"])
    assert error.value.code == "tag_creation_forbidden"
    assert _quarantine_keys() == before  # the stored object was deleted
    document = _create(people["member"], community, tags=["django"])
    assert [tag.name for tag in document.tags.all()] == ["Django"]
    document = _create(people["contributor"], community, tags=["Brand new"])
    assert [tag.name for tag in document.tags.all()] == ["Brand new"]


def test_rate_limit_counts_successful_uploads_only(people, community, settings):
    settings.POSTS_RATE_LIMITS = {**settings.POSTS_RATE_LIMITS, "document": 2}
    actor = people["contributor"]
    with pytest.raises(DomainError):
        _create(actor, community, title="")  # refused: not counted
    _create(actor, community)
    _create(actor, community)
    before = _quarantine_keys()
    with pytest.raises(DomainError) as error:
        _create(actor, community)
    assert error.value.code == "rate_limited"
    assert _quarantine_keys() == before
    assert Document.objects.count() == 2


def test_database_failure_deletes_stored_object(people, community, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(services, "refresh_search_vector", boom)
    before = _quarantine_keys()
    with pytest.raises(RuntimeError):
        _create(people["contributor"], community)
    assert _quarantine_keys() == before
    assert not Document.objects.exists()


def test_storage_failure_is_a_domain_error(people, community, monkeypatch):
    def boom(upload):
        raise OSError("bucket unreachable")

    monkeypatch.setattr(services.storage, "save_to_quarantine", boom)
    with pytest.raises(DomainError) as error:
        _create(people["contributor"], community)
    assert error.value.code == "storage_unavailable"


# --- add_version ------------------------------------------------------------------------


def test_add_version_keeps_reference_until_clean(
    people, community, make_document, django_capture_on_commit_callbacks
):
    owner = people["contributor"]
    document = make_document(community, owner)
    first = document.current_version
    with django_capture_on_commit_callbacks() as callbacks:
        version = services.add_version(
            actor=owner,
            document=document,
            upload=upload("guide-v2.docx", SAMPLES["docx"]),
            change_note="Second edition",
        )
    assert version.number == 2
    assert version.version_label == "2"
    assert version.scan_status == DocumentVersion.ScanStatus.PENDING
    assert version.promote_on_clean
    assert not version.is_reference
    document.refresh_from_db()
    assert document.current_version == first
    assert _audit("document.version_add").get().changes["version"] == "2"
    assert len(callbacks) == 1


def test_version_labels(people, community, make_document):
    owner = people["contributor"]
    document = make_document(community, owner)
    v2 = services.add_version(actor=owner, document=document, upload=upload(), version_label="3")
    assert v2.version_label == "3"
    v3 = services.add_version(actor=owner, document=document, upload=upload())
    assert (v3.number, v3.version_label) == (3, "3-2")
    with pytest.raises(DomainError) as error:
        services.add_version(actor=owner, document=document, upload=upload(), version_label="3")
    assert error.value.code == "version_label_taken"


def test_moderator_adds_version_to_someone_elses_document(people, community, make_document):
    document = make_document(community, people["contributor"])
    version = services.add_version(
        actor=people["moderator"], document=document, upload=upload(), make_reference=False
    )
    assert version.uploaded_by == people["moderator"]
    assert not version.promote_on_clean


def test_add_version_refusals(people, community, make_document):
    document = make_document(community, people["contributor"])
    for actor in (people["member"], people["outsider"]):
        with pytest.raises(DomainError) as error:
            services.add_version(actor=actor, document=document, upload=upload())
        assert error.value.code == "forbidden"
    Document.objects.filter(pk=document.pk).update(status=Document.Status.ARCHIVED)
    document.refresh_from_db()
    with pytest.raises(DomainError) as error:
        services.add_version(actor=people["contributor"], document=document, upload=upload())
    assert error.value.code == "invalid_state"


def test_owner_who_left_the_community_refused(people, community, make_document):
    owner = people["contributor"]
    document = make_document(community, owner)
    CommunityMembership.objects.filter(community=community, user=owner).delete()
    owner = type(owner).objects.get(pk=owner.pk)  # fresh instance: no membership cache
    with pytest.raises(DomainError) as error:
        services.add_version(actor=owner, document=document, upload=upload())
    assert error.value.code == "forbidden"
    with pytest.raises(DomainError) as error:
        _update(owner, document, title="Mine still?")
    assert error.value.code == "forbidden"


def test_add_version_in_suspended_community(people, community, make_document):
    document = make_document(community, people["contributor"])
    Community.objects.filter(pk=community.pk).update(status=Community.Status.SUSPENDED)
    document.community.refresh_from_db()
    with pytest.raises(DomainError) as error:
        services.add_version(actor=people["moderator"], document=document, upload=upload())
    assert error.value.code == "read_only"


# --- update_document --------------------------------------------------------------------


def _update(actor, document, **changes):
    fields = {
        "title": document.title,
        "description": document.description,
        "doc_type": document.doc_type,
        "tags": None,
        "visibility": document.visibility,
        "min_role": document.min_role,
        "download_min_role": document.download_min_role,
        "expires_at": document.expires_at,
        "review_due_at": document.review_due_at,
    }
    fields.update(changes)
    return services.update_document(actor=actor, document=document, **fields)


def test_moderator_edits_someone_elses_document(
    people, community, make_document, django_capture_on_commit_callbacks
):
    owner = people["contributor"]
    document = make_document(community, owner, title="Old title")
    with django_capture_on_commit_callbacks(execute=True):
        _update(
            people["moderator"],
            document,
            title="New title",
            visibility=Document.Visibility.RESTRICTED,
            min_role=Role.EXPERT,
            tags=["Kubernetes"],
        )
    document.refresh_from_db()
    assert document.title == "New title"
    assert document.min_role == Role.EXPERT
    event = _audit("document.update").get()
    assert event.actor == people["moderator"]
    assert event.changes["title"] == {"before": "Old title", "after": "New title"}
    assert event.changes["visibility"] == {"before": "community", "after": "restricted"}
    assert event.changes["tags"] == {"before": [], "after": ["Kubernetes"]}
    assert "description" not in event.changes
    assert Notification.objects.filter(recipient=owner, category="system").exists()
    assert list(Document.objects.filter(search_vector=_query("kubernetes"))) == [document]


def test_update_without_change_writes_nothing(people, community, make_document):
    document = make_document(community, people["contributor"])
    _update(people["contributor"], document)
    assert not _audit("document.update").exists()


def test_update_refused_for_member_and_expired_dates(people, community, make_document):
    document = make_document(community, people["contributor"])
    with pytest.raises(DomainError) as error:
        _update(people["member"], document, title="Hijack")
    assert error.value.code == "forbidden"
    with pytest.raises(DomainError) as error:
        _update(people["contributor"], document, expires_at=timezone.now() - timedelta(minutes=1))
    assert error.value.code == "invalid_date"


# --- reference version ------------------------------------------------------------------


def test_set_reference_version(people, community, make_document):
    owner = people["contributor"]
    document = make_document(community, owner)
    first = document.current_version
    second = services.add_version(actor=owner, document=document, upload=upload())
    with pytest.raises(DomainError) as error:
        services.set_reference_version(actor=owner, version=second)
    assert error.value.code == "invalid_state"  # still pending
    third = services.add_version(actor=owner, document=document, upload=upload())
    DocumentVersion.objects.filter(pk=second.pk).update(scan_status="clean")
    second.refresh_from_db()
    services.set_reference_version(actor=owner, version=second)
    document.refresh_from_db()
    first.refresh_from_db()
    third.refresh_from_db()
    assert document.current_version == second
    assert not first.is_reference
    assert DocumentVersion.objects.get(pk=second.pk).is_reference
    assert not third.promote_on_clean  # the explicit choice cancels the pending promotion
    event = _audit("document.reference_set").get()
    assert event.changes == {"version": {"before": "1", "after": "2"}}


def test_set_reference_refused_to_member(people, community, make_document):
    document = make_document(community, people["contributor"])
    with pytest.raises(DomainError) as error:
        services.set_reference_version(actor=people["member"], version=document.current_version)
    assert error.value.code == "forbidden"


# --- archive / restore ------------------------------------------------------------------


def test_archive_and_restore(people, community, make_document):
    owner = people["contributor"]
    document = make_document(community, owner)
    services.archive_document(actor=owner, document=document)
    assert document.status == Document.Status.ARCHIVED
    assert document.archived_by == owner
    with pytest.raises(DomainError) as error:
        services.archive_document(actor=owner, document=document)
    assert error.value.code == "invalid_state"
    services.restore_document(actor=people["moderator"], document=document)
    assert document.status == Document.Status.ACTIVE
    assert document.archived_at is None
    assert _audit("document.archive").count() == 1
    assert _audit("document.restore").get().actor == people["moderator"]


def test_restore_after_expiry_date_gives_expired(people, community, make_document):
    document = make_document(
        community,
        people["contributor"],
        status=Document.Status.ARCHIVED,
        expires_at=timezone.now() - timedelta(days=1),
    )
    services.restore_document(actor=people["moderator"], document=document)
    assert document.status == Document.Status.EXPIRED


def test_archive_refusals(people, community, make_document):
    document = make_document(community, people["contributor"])
    with pytest.raises(DomainError) as error:
        services.archive_document(actor=people["member"], document=document)
    assert error.value.code == "forbidden"
    with pytest.raises(DomainError) as error:
        services.restore_document(actor=people["contributor"], document=document)
    assert error.value.code == "invalid_state"


def test_moderator_archives_in_suspended_community(people, community, make_document):
    document = make_document(community, people["contributor"])
    Community.objects.filter(pk=community.pk).update(status=Community.Status.SUSPENDED)
    document.community.refresh_from_db()
    services.archive_document(actor=people["moderator"], document=document)
    assert document.status == Document.Status.ARCHIVED


# --- links ------------------------------------------------------------------------------


def test_link_and_unlink(people, community, make_document):
    owner = people["contributor"]
    document = make_document(community, owner)
    post = make_post(community, people["member"])
    link = services.link_document(actor=owner, document=document, post=post)
    assert link.post == post and link.created_by == owner
    assert services.link_document(actor=owner, document=document, post=post) == link
    assert _audit("document.link").count() == 1
    services.unlink_document(actor=owner, link=link)
    assert not DocumentLink.objects.exists()
    assert _audit("document.unlink").get().changes == {"post": str(post.public_id)}


def test_link_refusals(people, community, make_document, make_community, make_user):
    owner = people["contributor"]
    document = make_document(community, owner)
    other = make_community("Other guild", access_mode=Community.AccessMode.INVITE)
    hidden_post = make_post(other, make_user("someone@example.com"))
    with pytest.raises(DomainError) as error:
        services.link_document(actor=owner, document=document, post=hidden_post)
    assert error.value.code == "forbidden"  # the actor cannot see the post
    open_other = make_community("Open guild")
    visible_post = make_post(open_other, people["member"])
    with pytest.raises(DomainError) as error:
        services.link_document(actor=owner, document=document, post=visible_post)
    assert error.value.code == "invalid_link"
    post = make_post(community, owner)
    with pytest.raises(DomainError) as error:
        services.link_document(actor=people["member"], document=document, post=post)
    assert error.value.code == "forbidden"
    link = services.link_document(actor=owner, document=document, post=post)
    with pytest.raises(DomainError) as error:
        services.unlink_document(actor=people["member"], link=link)
    assert error.value.code == "forbidden"
