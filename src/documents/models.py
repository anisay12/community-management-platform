"""Community documents (L5): metadata, versions, links to other content and downloads.

A ``Document`` belongs to a community and points at its reference ``DocumentVersion``
(``current_version``). Each version is a file in the private object store: uploaded under
``quarantine/``, scanned by ClamAV (``documents.tasks.scan_document_version``), then moved under
``documents/`` once clean. Only clean versions are ever downloadable, and only through the
application (``X-Accel-Redirect``, ADR-0001). ``download_count`` is a deferred counter.
"""

import functools
import operator
import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from communities.models import TimestampedModel
from communities.roles import CommunityRole

DESCRIPTION_MAX_LENGTH = 5_000
CHANGE_NOTE_MAX_LENGTH = 500


def _exactly_one(*fields: str) -> models.Q:
    """``Q`` true when exactly one of the nullable foreign keys ``fields`` is set."""
    branches = []
    for field in fields:
        branch = models.Q(**{f"{field}__isnull": False})
        for other in fields:
            if other != field:
                branch &= models.Q(**{f"{other}__isnull": True})
        branches.append(branch)
    return functools.reduce(operator.or_, branches)


class DocumentQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Documents ``user`` may open (see ``documents.selectors.visible_documents_q``)."""
        from .selectors import visible_documents_q

        condition = visible_documents_q(user)
        return self.none() if condition is None else self.filter(condition)


class Document(TimestampedModel):
    class DocType(models.TextChoices):
        GUIDE = "guide", _("Guide")
        TEMPLATE = "template", _("Template")
        PRESENTATION = "presentation", _("Presentation")
        REFERENCE = "reference", _("Reference")
        CODE_SAMPLE = "code_sample", _("Code sample")
        OTHER = "other", _("Other")

    class Visibility(models.TextChoices):
        COMMUNITY = "community", _("Everyone who can read the community")
        RESTRICTED = "restricted", _("Members with a minimum role")

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        ARCHIVED = "archived", _("Archived")
        EXPIRED = "expired", _("Expired")

    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    community = models.ForeignKey(
        "communities.Community",
        on_delete=models.PROTECT,
        related_name="documents",
        verbose_name=_("community"),
    )
    title = models.CharField(_("title"), max_length=200)
    description = models.TextField(_("description"), max_length=DESCRIPTION_MAX_LENGTH, blank=True)
    doc_type = models.CharField(
        _("type"), max_length=16, choices=DocType.choices, default=DocType.OTHER
    )
    tags = models.ManyToManyField(
        "taxonomy.Tag", blank=True, related_name="documents", verbose_name=_("tags")
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents",
        verbose_name=_("owner"),
    )
    owner_display = models.CharField(_("owner name"), max_length=150)
    visibility = models.CharField(
        _("visibility"), max_length=16, choices=Visibility.choices, default=Visibility.COMMUNITY
    )
    # Minimum community role to read a ``restricted`` document (ignored otherwise).
    min_role = models.CharField(
        _("minimum role to read"),
        max_length=16,
        choices=CommunityRole.choices,
        default=CommunityRole.MEMBER,
    )
    download_min_role = models.CharField(
        _("minimum role to download"),
        max_length=16,
        choices=CommunityRole.choices,
        default=CommunityRole.MEMBER,
    )
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    review_due_at = models.DateTimeField(_("review due at"), null=True, blank=True)
    current_version = models.ForeignKey(
        "DocumentVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("reference version"),
    )
    download_count = models.PositiveIntegerField(_("download count"), default=0)
    archived_at = models.DateTimeField(_("archived at"), null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("archived by"),
    )
    search_vector = SearchVectorField(_("search vector"), null=True, editable=False)

    objects = DocumentQuerySet.as_manager()

    class Meta:
        ordering = ["-updated_at", "-pk"]
        indexes = [
            models.Index(fields=["community", "doc_type"], name="document_community_type_idx"),
            models.Index(
                "community", "status", models.F("updated_at").desc(), name="document_list_idx"
            ),
            models.Index(
                fields=["expires_at"],
                condition=models.Q(status="active", expires_at__isnull=False),
                name="document_expiry_idx",
            ),
            GinIndex(fields=["search_vector"], name="document_search_gin"),
        ]
        verbose_name = _("document")
        verbose_name_plural = _("documents")

    def __str__(self) -> str:
        return self.title

    @property
    def is_restricted(self) -> bool:
        return self.visibility == self.Visibility.RESTRICTED


class DocumentVersion(models.Model):
    class ScanStatus(models.TextChoices):
        PENDING = "pending", _("Scan in progress")
        CLEAN = "clean", _("Clean")
        INFECTED = "infected", _("Infected")
        ERROR = "error", _("Scan failed")

    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="versions", verbose_name=_("document")
    )
    # 1, 2, 3... per document; ``version_label`` defaults to it and appears in the UI and URLs.
    number = models.PositiveIntegerField(_("number"))
    version_label = models.CharField(_("version"), max_length=40)
    # Random object key without the original name: ``quarantine/<hex>`` until the scan is
    # clean, then ``documents/<hex>``. Empty once the object of an infected version is deleted.
    storage_key = models.CharField(_("storage key"), max_length=255, blank=True)
    original_filename = models.CharField(_("file name"), max_length=255)
    mime_type = models.CharField(_("content type"), max_length=127)
    size = models.PositiveBigIntegerField(_("size"))
    sha256 = models.CharField(_("SHA-256"), max_length=64)
    scan_status = models.CharField(
        _("scan status"), max_length=16, choices=ScanStatus.choices, default=ScanStatus.PENDING
    )
    scanned_at = models.DateTimeField(_("scanned at"), null=True, blank=True)
    # Signature name of an infected file, or the reason of a scan error (never file content).
    scan_detail = models.CharField(_("scan detail"), max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="document_versions",
        verbose_name=_("uploaded by"),
    )
    change_note = models.CharField(_("change note"), max_length=CHANGE_NOTE_MAX_LENGTH, blank=True)
    is_reference = models.BooleanField(_("reference version"), default=False)
    # Asked at upload: become the reference version once the scan finds the file clean (the
    # current reference stays in place until then).
    promote_on_clean = models.BooleanField(_("make reference once clean"), default=False)
    created_at = models.DateTimeField(_("created at"), default=timezone.now)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["document", "number"], name="version_number_unique"),
            models.UniqueConstraint(
                fields=["document"],
                condition=models.Q(is_reference=True),
                name="version_one_reference",
            ),
            models.UniqueConstraint(
                fields=["storage_key"],
                condition=~models.Q(storage_key=""),
                name="version_storage_key_unique",
            ),
        ]
        indexes = [
            models.Index("document", models.F("created_at").desc(), name="version_document_idx"),
            models.Index(
                fields=["scan_status", "created_at"],
                condition=~models.Q(scan_status="clean"),
                name="version_scan_idx",
            ),
        ]
        verbose_name = _("document version")
        verbose_name_plural = _("document versions")

    def __str__(self) -> str:
        return f"{self.document_id} v{self.version_label}"

    @property
    def is_clean(self) -> bool:
        return self.scan_status == self.ScanStatus.CLEAN


class DocumentLink(models.Model):
    """A typed link from a document to other content. L5 links posts; REX (L6) and events (L7)
    add their nullable foreign key and widen the CHECK."""

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="links", verbose_name=_("document")
    )
    post = models.ForeignKey(
        "posts.Post",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="document_links",
        verbose_name=_("post"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("created by"),
    )
    created_at = models.DateTimeField(_("created at"), default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=_exactly_one("post"), name="document_link_exactly_one_target"
            ),
            models.UniqueConstraint(
                fields=["document", "post"],
                condition=models.Q(post__isnull=False),
                name="document_link_unique_post",
            ),
        ]
        verbose_name = _("document link")
        verbose_name_plural = _("document links")

    def __str__(self) -> str:
        return f"{self.document_id} -> post {self.post_id}"


class DownloadLog(models.Model):
    """One download (or preview) of a version: statistics of the most consulted resources and
    traceability of restricted documents. Purged after ``DOWNLOAD_LOG_RETENTION_DAYS``; deleted
    with the user on erasure."""

    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="downloads", verbose_name=_("document")
    )
    version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.CASCADE,
        related_name="downloads",
        verbose_name=_("version"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="document_downloads",
        verbose_name=_("user"),
    )
    is_preview = models.BooleanField(_("preview"), default=False)
    created_at = models.DateTimeField(_("created at"), default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index("document", models.F("created_at").desc(), name="download_document_idx"),
            models.Index(fields=["created_at"], name="download_created_idx"),
        ]
        verbose_name = _("download")
        verbose_name_plural = _("downloads")

    def __str__(self) -> str:
        return f"{self.user_id} <- {self.version_id}"
