"""Write services on documents (L5): upload, versions, metadata, reference version, archiving
and links to posts.

Every service is keyword-only with the actor first, re-checks its policy and raises
``core.errors.DomainError`` (``forbidden``, ``read_only``, ``not_member``, ``invalid_state``...)
on refusal; writes run in ``transaction.atomic`` and are audited in the same transaction.

Uploads: the file is validated (``validation.validate_upload``) and streamed to
``quarantine/<hex>`` *before* the database transaction opens (a long upload never holds row
locks); the transaction then re-checks the rights and writes the rows. If anything fails after
the object was stored, the object is deleted. The antivirus scan
(``tasks.scan_document_version``) is queued once the transaction commits; it promotes the file
and, when asked, the version.

Decisions where the spec is silent:

- An upload counts against the hourly ``document`` rate limit (``posts.ratelimit``, bucket in
  ``POSTS_RATE_LIMITS``) for a new document *and* for a new version, once every check passed.
- ``expires_at`` must be in the future when it is set or changed (``invalid_date``).
- The default label of a new version is its number; when another version already uses that
  label, the first free ``<number>-2``, ``<number>-3``... is used. An explicit label that is
  taken is refused (``version_label_taken``).
- Choosing the reference version by hand cancels the pending promotion of versions still being
  scanned (the explicit choice wins over an earlier "make it the reference" intent).
- Linking a document to a post it is already linked to returns the existing link (idempotent,
  no second audit event).
- A moderator who edits someone else's document notifies the owner (category ``system``), as
  for posts.
- If the caller wraps a service in an outer transaction that later rolls back, the quarantined
  object stays orphaned (never downloadable: no row points to it).
"""

from django.conf import settings
from django.contrib.postgres.aggregates import StringAgg
from django.contrib.postgres.search import SearchVector
from django.db import transaction
from django.db.models import Max, OuterRef, Subquery, TextField, Value
from django.db.models.functions import Coalesce
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from audit.services import record
from communities.policies import membership_of
from communities.roles import CommunityRole
from core.errors import DomainError
from core.tasks import delay_on_commit
from core.templatetags.component_tags import member_name
from notifications.services import notify
from posts import ratelimit
from posts.policies import can_view_post

# Tag resolution shared with posts: existing tags always, new ones for contributors+
# (``posts.policies.can_create_tag``), held against a concurrent merge.
from posts.services_posts import _resolve_tags

from . import policies, storage, tasks
from .models import (
    CHANGE_NOTE_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    Document,
    DocumentLink,
    DocumentVersion,
)
from .validation import validate_upload

RATE_LIMIT_BUCKET = "document"
EDITABLE_FIELDS = (
    "title",
    "description",
    "doc_type",
    "visibility",
    "min_role",
    "download_min_role",
    "expires_at",
    "review_due_at",
)


# --- errors -----------------------------------------------------------------------------


def _forbidden():
    return DomainError("forbidden", _("You are not allowed to perform this action."))


def _invalid_state():
    return DomainError("invalid_state", _("This action is not possible in the current state."))


def _ensure_live(community) -> None:
    if community.is_read_only:
        raise DomainError(
            "read_only", _("This community is suspended or archived: it cannot be changed.")
        )


# --- helpers ----------------------------------------------------------------------------


def _lock(document: Document) -> Document:
    """Re-read ``document`` under a row lock (in place) so concurrent writes serialise."""
    document.refresh_from_db(from_queryset=Document.objects.select_for_update())
    return document


def _owner_display(user) -> str:
    max_length = Document._meta.get_field("owner_display").max_length
    with translation.override(settings.LANGUAGE_CODE):
        return member_name(user)[:max_length]


def _choice(value, choices, field: str) -> str:
    value = str(value)
    if value not in choices:
        raise DomainError(
            "invalid_choice", _("The value of “%(field)s” is not valid.") % {"field": field}
        )
    return value


def _clean_metadata(
    *,
    title,
    description,
    doc_type,
    visibility,
    min_role,
    download_min_role,
    expires_at,
    review_due_at,
    previous_expires_at=None,
) -> dict:
    title = " ".join((title or "").split())
    if not title:
        raise DomainError("title_required", _("Give the document a title."))
    if len(title) > Document._meta.get_field("title").max_length:
        raise DomainError("title_too_long", _("The title is too long."))
    description = (description or "").strip()
    if len(description) > DESCRIPTION_MAX_LENGTH:
        raise DomainError(
            "description_too_long",
            _("The description is too long (at most %(limit)s characters).")
            % {"limit": DESCRIPTION_MAX_LENGTH},
        )
    if (
        expires_at is not None
        and expires_at != previous_expires_at
        and expires_at <= timezone.now()
    ):
        raise DomainError("invalid_date", _("The expiry date must be in the future."))
    return {
        "title": title,
        "description": description,
        "doc_type": _choice(doc_type, Document.DocType.values, "doc_type"),
        "visibility": _choice(visibility, Document.Visibility.values, "visibility"),
        "min_role": _choice(min_role, CommunityRole.values, "min_role"),
        "download_min_role": _choice(download_min_role, CommunityRole.values, "download_min_role"),
        "expires_at": expires_at,
        "review_due_at": review_due_at,
    }


def _clean_version_fields(change_note, version_label) -> tuple[str, str]:
    change_note = (change_note or "").strip()
    if len(change_note) > CHANGE_NOTE_MAX_LENGTH:
        raise DomainError(
            "change_note_too_long",
            _("The change note is too long (at most %(limit)s characters).")
            % {"limit": CHANGE_NOTE_MAX_LENGTH},
        )
    version_label = " ".join((version_label or "").split())
    if len(version_label) > DocumentVersion._meta.get_field("version_label").max_length:
        raise DomainError("version_label_too_long", _("The version label is too long."))
    return change_note, version_label


def _version_label(document: Document, number: int, requested: str) -> str:
    """``requested`` if free, else refused; the number (or a free variant) by default."""
    taken = set(document.versions.values_list("version_label", flat=True))
    if requested:
        if requested in taken:
            raise DomainError(
                "version_label_taken",
                _("Another version of this document is already labelled “%(label)s”.")
                % {"label": requested},
            )
        return requested
    label, suffix = str(number), 1
    while label in taken:
        suffix += 1
        label = f"{number}-{suffix}"
    return label


def _search_vector() -> SearchVector:
    """Title (A), tag names (B) and description (C), ``simple`` configuration (as posts)."""
    tag_names = (
        Document.tags.through.objects.filter(document_id=OuterRef("pk"))
        .order_by()
        .values("document_id")
        .annotate(names=StringAgg("tag__name", delimiter=" ", order_by="tag__name"))
        .values("names")
    )
    return (
        SearchVector("title", weight="A", config="simple")
        + SearchVector(
            Coalesce(
                Subquery(tag_names, output_field=TextField()),
                Value("", output_field=TextField()),
            ),
            weight="B",
            config="simple",
        )
        + SearchVector("description", weight="C", config="simple")
    )


def refresh_search_vectors(documents) -> int:
    """Recompute the search vector of every document of the queryset in one UPDATE."""
    return documents.update(search_vector=_search_vector())


def refresh_search_vector(document: Document) -> None:
    refresh_search_vectors(Document.objects.filter(pk=document.pk))


def _ensure_can_upload(actor, community) -> None:
    _ensure_live(community)
    if policies.can_upload(actor, community):
        return
    if membership_of(actor, community) is None:
        raise DomainError("not_member", _("You are not a member of this community."))
    raise _forbidden()


def _ensure_can_edit(actor, document: Document) -> None:
    """Rights first (``forbidden`` reveals nothing), then the community and document state."""
    if not policies.can_view_document(actor, document):
        raise _forbidden()
    _ensure_live(document.community)
    if document.status == Document.Status.ARCHIVED:
        raise _invalid_state()
    if not policies.can_edit_document(actor, document):
        raise _forbidden()


def _store(upload):
    """Validate ``upload`` and stream it to quarantine: ``(ValidatedUpload, key)``."""
    validated = validate_upload(upload)
    try:
        key = storage.save_to_quarantine(upload)
    except Exception as exc:
        raise DomainError(
            "storage_unavailable",
            _("The file could not be stored right now. Please try again later."),
        ) from exc
    return validated, key


def _create_version(*, document, number, label, validated, key, actor, change_note, **extra):
    return DocumentVersion.objects.create(
        document=document,
        number=number,
        version_label=label,
        storage_key=key,
        original_filename=validated.filename,
        mime_type=validated.mime_type,
        size=validated.size,
        sha256=validated.sha256,
        scan_status=DocumentVersion.ScanStatus.PENDING,
        uploaded_by=actor,
        change_note=change_note,
        **extra,
    )


def switch_reference(document: Document, version: DocumentVersion) -> None:
    """Make ``version`` the reference of ``document`` (both locked by the caller).

    The previous reference loses its flag first: the partial unique index
    ``version_one_reference`` is checked statement by statement.
    """
    DocumentVersion.objects.filter(document=document, is_reference=True).exclude(
        pk=version.pk
    ).update(is_reference=False)
    if not version.is_reference or version.promote_on_clean:
        version.is_reference = True
        version.promote_on_clean = False
        version.save(update_fields=["is_reference", "promote_on_clean"])
    document.current_version = version
    document.save(update_fields=["current_version", "updated_at"])


def _audit_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _notify_owner(document: Document, actor) -> None:
    owner = document.owner
    if owner is not None and owner.pk != actor.pk and policies.can_view_document(owner, document):
        notify("system", [owner], actor=actor, target=document, community=document.community)


# --- upload -----------------------------------------------------------------------------


def create_document(
    *,
    actor,
    community,
    upload,
    title,
    description="",
    doc_type=Document.DocType.OTHER,
    tags=(),
    visibility=Document.Visibility.COMMUNITY,
    min_role=CommunityRole.MEMBER,
    download_min_role=CommunityRole.MEMBER,
    expires_at=None,
    review_due_at=None,
    change_note="",
    version_label="",
) -> Document:
    """Create a document from ``upload``: version 1, pending its antivirus scan.

    The version is the reference from the start (``is_reference``, ``current_version``) but
    nobody except the document's managers sees the document until the scan finds it clean.
    """
    _ensure_can_upload(actor, community)
    fields = _clean_metadata(
        title=title,
        description=description,
        doc_type=doc_type,
        visibility=visibility,
        min_role=min_role,
        download_min_role=download_min_role,
        expires_at=expires_at,
        review_due_at=review_due_at,
    )
    change_note, version_label = _clean_version_fields(change_note, version_label)
    validated, key = _store(upload)
    try:
        with transaction.atomic():
            # The upload may have taken minutes: the rights are checked again.
            community.refresh_from_db(fields=["status", "allow_member_uploads"])
            _ensure_can_upload(actor, community)
            tag_objects = _resolve_tags(actor, community, tags)
            # Counted once every check has passed, right before the write.
            ratelimit.hit(actor, RATE_LIMIT_BUCKET)
            document = Document.objects.create(
                community=community, owner=actor, owner_display=_owner_display(actor), **fields
            )
            document.tags.set(tag_objects)
            version = _create_version(
                document=document,
                number=1,
                label=version_label or "1",
                validated=validated,
                key=key,
                actor=actor,
                change_note=change_note,
                is_reference=True,
            )
            document.current_version = version
            document.save(update_fields=["current_version"])
            refresh_search_vector(document)
            record(
                actor=actor,
                action="document.create",
                target=document,
                changes={
                    "title": document.title,
                    "version": version.version_label,
                    "filename": version.original_filename,
                    "sha256": version.sha256,
                },
                community=community,
            )
            delay_on_commit(tasks.scan_document_version, version.pk)
    except BaseException:
        storage.discard(key)
        raise
    return document


def add_version(
    *, actor, document, upload, change_note="", version_label="", make_reference=True
) -> DocumentVersion:
    """Add a version to ``document`` (pending its scan).

    The current reference stays in place; with ``make_reference`` the scan task makes the new
    version the reference once it is clean (``promote_on_clean``).
    """
    _ensure_can_edit(actor, document)
    change_note, version_label = _clean_version_fields(change_note, version_label)
    validated, key = _store(upload)
    try:
        with transaction.atomic():
            _lock(document)
            _ensure_can_edit(actor, document)
            number = (document.versions.aggregate(top=Max("number"))["top"] or 0) + 1
            label = _version_label(document, number, version_label)
            ratelimit.hit(actor, RATE_LIMIT_BUCKET)
            version = _create_version(
                document=document,
                number=number,
                label=label,
                validated=validated,
                key=key,
                actor=actor,
                change_note=change_note,
                promote_on_clean=bool(make_reference),
            )
            Document.objects.filter(pk=document.pk).update(updated_at=timezone.now())
            record(
                actor=actor,
                action="document.version_add",
                target=document,
                changes={
                    "version": label,
                    "number": number,
                    "filename": version.original_filename,
                    "sha256": version.sha256,
                    "make_reference": bool(make_reference),
                },
                community=document.community,
            )
            delay_on_commit(tasks.scan_document_version, version.pk)
    except BaseException:
        storage.discard(key)
        raise
    return version


# --- metadata ---------------------------------------------------------------------------


@transaction.atomic
def update_document(
    *,
    actor,
    document,
    title,
    description,
    doc_type,
    tags,
    visibility,
    min_role,
    download_min_role,
    expires_at,
    review_due_at,
) -> Document:
    """Edit the metadata of ``document`` (``tags=None`` keeps the tags). The audit event lists
    the changed fields with their values before and after; nothing is written when nothing
    changed."""
    _lock(document)
    _ensure_can_edit(actor, document)
    fields = _clean_metadata(
        title=title,
        description=description,
        doc_type=doc_type,
        visibility=visibility,
        min_role=min_role,
        download_min_role=download_min_role,
        expires_at=expires_at,
        review_due_at=review_due_at,
        previous_expires_at=document.expires_at,
    )
    changes = {}
    for name in EDITABLE_FIELDS:
        before, after = getattr(document, name), fields[name]
        if before != after:
            changes[name] = {"before": _audit_value(before), "after": _audit_value(after)}
            setattr(document, name, after)
    if tags is not None:
        tag_objects = _resolve_tags(actor, document.community, tags)
        before = sorted(tag.name for tag in document.tags.all())
        after = sorted(tag.name for tag in tag_objects)
        if before != after:
            changes["tags"] = {"before": before, "after": after}
            document.tags.set(tag_objects)
    if not changes:
        return document
    document.save(update_fields=[*EDITABLE_FIELDS, "updated_at"])
    refresh_search_vector(document)
    record(
        actor=actor,
        action="document.update",
        target=document,
        changes=changes,
        community=document.community,
    )
    _notify_owner(document, actor)
    return document


@transaction.atomic
def set_reference_version(*, actor, version) -> DocumentVersion:
    """Make the clean ``version`` the reference of its document."""
    document = version.document
    _lock(document)
    version.refresh_from_db(from_queryset=DocumentVersion.objects.select_for_update())
    _ensure_can_edit(actor, document)
    if not policies.can_set_reference(actor, version):
        raise _invalid_state()  # rights checked above: the version is not clean
    previous = document.current_version
    if previous is not None and previous.pk == version.pk and version.is_reference:
        return version
    # An explicit choice cancels earlier "make it the reference once clean" intents.
    DocumentVersion.objects.filter(document=document, promote_on_clean=True).exclude(
        pk=version.pk
    ).update(promote_on_clean=False)
    switch_reference(document, version)
    record(
        actor=actor,
        action="document.reference_set",
        target=document,
        changes={
            "version": {
                "before": previous.version_label if previous else None,
                "after": version.version_label,
            }
        },
        community=document.community,
    )
    return version


# --- archiving --------------------------------------------------------------------------


@transaction.atomic
def archive_document(*, actor, document) -> Document:
    """Archive ``document`` (owner or moderator; works in read-only communities)."""
    _lock(document)
    if not policies.can_archive_document(actor, document):
        raise _forbidden()
    if document.status == Document.Status.ARCHIVED:
        raise _invalid_state()
    previous = document.status
    document.status = Document.Status.ARCHIVED
    document.archived_at = timezone.now()
    document.archived_by = actor
    document.save(update_fields=["status", "archived_at", "archived_by", "updated_at"])
    record(
        actor=actor,
        action="document.archive",
        target=document,
        changes={"status": {"before": previous, "after": document.status}},
        community=document.community,
    )
    return document


@transaction.atomic
def restore_document(*, actor, document) -> Document:
    """Restore an archived document: ``active``, or ``expired`` when ``expires_at`` passed."""
    _lock(document)
    if not policies.can_archive_document(actor, document):
        raise _forbidden()
    if document.status != Document.Status.ARCHIVED:
        raise _invalid_state()
    expired = document.expires_at is not None and document.expires_at <= timezone.now()
    document.status = Document.Status.EXPIRED if expired else Document.Status.ACTIVE
    document.archived_at = None
    document.archived_by = None
    document.save(update_fields=["status", "archived_at", "archived_by", "updated_at"])
    record(
        actor=actor,
        action="document.restore",
        target=document,
        changes={"status": {"before": Document.Status.ARCHIVED, "after": document.status}},
        community=document.community,
    )
    return document


# --- links ------------------------------------------------------------------------------


@transaction.atomic
def link_document(*, actor, document, post) -> DocumentLink:
    """Link ``document`` to ``post`` (same community; the actor must see the post and be able
    to edit the document)."""
    if not can_view_post(actor, post):
        raise _forbidden()
    _lock(document)
    _ensure_can_edit(actor, document)
    if post.community_id != document.community_id:
        raise DomainError(
            "invalid_link", _("A document can only be linked to content of its community.")
        )
    if not policies.can_link_document(actor, document, post):
        raise _forbidden()
    existing = DocumentLink.objects.filter(document=document, post=post).first()
    if existing is not None:
        return existing
    link = DocumentLink.objects.create(document=document, post=post, created_by=actor)
    record(
        actor=actor,
        action="document.link",
        target=document,
        changes={"post": str(post.public_id)},
        community=document.community,
    )
    return link


@transaction.atomic
def unlink_document(*, actor, link) -> None:
    """Remove ``link`` (whoever may edit its document)."""
    document = link.document
    _lock(document)
    _ensure_can_edit(actor, document)
    post_id = link.post.public_id if link.post_id else None
    deleted, _rows = DocumentLink.objects.filter(pk=link.pk).delete()
    if not deleted:
        raise _invalid_state()
    record(
        actor=actor,
        action="document.unlink",
        target=document,
        changes={"post": str(post_id) if post_id else None},
        community=document.community,
    )
