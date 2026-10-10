"""Deferred work on documents.

``download_count`` is a deferred counter (spec § 2): the download view records a
``DownloadLog`` row and calls ``schedule_download_recount``, which enqueues one
``recount_downloads`` per document and lock window once the transaction commits;
``verify_download_counters`` repairs any drift every night.

Antivirus scan of uploaded versions (``scan_document_version``), broadcast of newly published
documents (``broadcast_document``) and the daily lifecycle jobs (``expire_documents``,
``purge_download_logs``) live here too.
"""

from datetime import timedelta

import structlog
from celery import shared_task
from celery.utils.time import get_exponential_backoff_interval
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from audit.services import record
from communities.models import CommunityMembership
from communities.policies import MEMBERSHIP_CACHE_ATTR
from core.tasks import delay_on_commit
from notifications.services import notify

from . import policies, scanner, storage
from .models import Document, DocumentVersion, DownloadLog

logger = structlog.get_logger(__name__)

RECOUNT_DELAY_SECONDS = 3
RECOUNT_LOCK_SECONDS = 10
PURGE_BATCH_SIZE = 5000


def _lock_key(document_pk: int) -> str:
    return f"recount:documents.document:{document_pk}"


def _recount(document_pk: int) -> int:
    """Store the current download count of one document; returns 1 when it changed."""
    total = DownloadLog.objects.filter(document_id=document_pk, is_preview=False).count()
    return (
        Document.objects.filter(pk=document_pk)
        .exclude(download_count=total)
        .update(download_count=total)
    )


@shared_task
def recount_downloads(document_pk: int) -> None:
    """Recompute ``download_count`` of one document (downloads, previews excluded)."""
    try:
        cache.delete(_lock_key(document_pk))
    except Exception:  # the lock only deduplicates; counting must still happen
        logger.warning("documents.recount.cache_unavailable", exc_info=True)
    _recount(document_pk)


def _enqueue(document_pk: int) -> None:
    key = _lock_key(document_pk)
    try:
        if not cache.add(key, 1, RECOUNT_LOCK_SECONDS):
            return  # a recount is already queued and has not started yet
    except Exception:  # cache down: enqueue anyway (duplicates are harmless)
        logger.warning("documents.recount.cache_unavailable", exc_info=True)
    try:
        recount_downloads.apply_async((document_pk,), countdown=RECOUNT_DELAY_SECONDS)
    except Exception:  # broker down: the nightly verification fixes the drift
        logger.warning("documents.recount.enqueue_failed", pk=document_pk, exc_info=True)
        try:
            cache.delete(key)
        except Exception:
            logger.warning("documents.recount.cache_unavailable", exc_info=True)


def schedule_download_recount(document) -> None:
    """Schedule ``recount_downloads`` for ``document`` on commit (deduplicated)."""
    document_pk = document.pk
    transaction.on_commit(lambda: _enqueue(document_pk))


# --- antivirus scan ---------------------------------------------------------------------

SCAN_MAX_RETRIES = 5
SCAN_RETRY_BACKOFF_SECONDS = 30  # 30 s, 60 s, 120 s, 240 s, 480 s (with jitter)
SCAN_RETRY_BACKOFF_MAX_SECONDS = 900
BROADCAST_BATCH_SIZE = 500
PUBLISHED_LEVELS = (
    CommunityMembership.NotificationLevel.ALL,
    CommunityMembership.NotificationLevel.HIGHLIGHTS,
)


def _locked_pending(version_pk: int):
    """``(document, version)`` under row locks (document first), or ``None`` when the version
    is gone or no longer pending (the scan already concluded: idempotence)."""
    document_pk = (
        DocumentVersion.objects.filter(pk=version_pk).values_list("document_id", flat=True).first()
    )
    if document_pk is None:
        return None
    document = (
        Document.objects.select_for_update(of=("self",))
        .select_related("community", "owner")
        .filter(pk=document_pk)
        .first()
    )
    version = (
        DocumentVersion.objects.select_for_update()
        .filter(pk=version_pk, scan_status=DocumentVersion.ScanStatus.PENDING)
        .first()
    )
    if document is None or version is None:
        return None
    return document, version


def _mark_error(version_pk: int, detail: str) -> None:
    with transaction.atomic():
        locked = _locked_pending(version_pk)
        if locked is None:
            return
        _document, version = locked
        version.scan_status = DocumentVersion.ScanStatus.ERROR
        version.scan_detail = detail[:255]
        version.scanned_at = timezone.now()
        version.save(update_fields=["scan_status", "scan_detail", "scanned_at"])
    logger.error("documents.scan.failed", version=version_pk, detail=detail)


def _scan(key: str):
    with storage.open_stream(key) as stream:
        return scanner.scan_stream(stream)


def _conclude_clean(version_pk: int, key: str) -> None:
    from .services import switch_reference

    # Moved before the rows are updated; ``promote`` is safe to repeat if this run dies.
    new_key = storage.promote(key)
    with transaction.atomic():
        locked = _locked_pending(version_pk)
        if locked is None:
            return
        document, version = locked
        version.storage_key = new_key
        version.scan_status = DocumentVersion.ScanStatus.CLEAN
        version.scan_detail = ""
        version.scanned_at = timezone.now()
        version.save(update_fields=["storage_key", "scan_status", "scan_detail", "scanned_at"])
        reference = document.current_version
        reference_unusable = reference is None or (
            reference.pk != version.pk and not reference.is_clean
        )
        if version.number == 1 or version.promote_on_clean or reference_unusable:
            switch_reference(document, version)
        if version.number == 1:
            delay_on_commit(broadcast_document, document.pk)
    logger.info("documents.scan.clean", version=version_pk)


def _conclude_infected(version_pk: int, key: str, signature: str) -> None:
    from posts.selectors import community_moderators

    with transaction.atomic():
        locked = _locked_pending(version_pk)
        if locked is None:
            return
        document, version = locked
        version.storage_key = ""
        version.scan_status = DocumentVersion.ScanStatus.INFECTED
        version.scan_detail = signature[:255]
        version.scanned_at = timezone.now()
        version.promote_on_clean = False
        version.save(
            update_fields=[
                "storage_key",
                "scan_status",
                "scan_detail",
                "scanned_at",
                "promote_on_clean",
            ]
        )
        record(
            actor=None,
            action="document.scan_infected",
            target=document,
            changes={"version": version.version_label, "signature": version.scan_detail},
            community=document.community,
        )
        recipients = [version.uploaded_by, *community_moderators(document.community)]
        notify(
            "document_infected",
            [user for user in recipients if user is not None],
            target=document,
            community=document.community,
        )
        # Deleted inside the transaction: if the delete fails, the version stays pending and
        # the scan is retried; an infected object never outlives a committed verdict.
        storage.delete(key)
    logger.warning("documents.scan.infected", version=version_pk, signature=signature)


@shared_task(bind=True, max_retries=SCAN_MAX_RETRIES)
def scan_document_version(self, version_pk: int) -> None:
    """Scan one pending version with ClamAV and conclude: ``clean``, ``infected`` or ``error``.

    - Clean: the object moves to ``documents/`` (``storage.promote``); the version becomes
      the reference when it is version 1, when it was uploaded with ``make_reference``
      (``promote_on_clean``) or when the document has no clean reference to keep; the first
      clean version of a document is broadcast (``broadcast_document``).
    - Infected: the object is deleted, ``storage_key`` emptied, the signature kept in
      ``scan_detail``; audited (``document.scan_infected``, no actor) and the uploader and the
      community's moderators are notified (``document_infected``).
    - ``ScannerUnavailable``: retried ``SCAN_MAX_RETRIES`` times with exponential backoff
      (what ``autoretry_for``/``retry_backoff`` do, written out so that the last failure can
      mark the version ``error`` with the reason in ``scan_detail``).

    A version that is no longer ``pending`` is left alone (idempotent). The scanner is called
    through ``documents.scanner.scan_stream`` so tests can replace it.
    """
    version = DocumentVersion.objects.filter(
        pk=version_pk, scan_status=DocumentVersion.ScanStatus.PENDING
    ).first()
    if version is None:
        return
    key = version.storage_key
    try:
        result = _scan(key)
    except scanner.ScannerUnavailable as exc:
        if self.request.retries >= SCAN_MAX_RETRIES:
            _mark_error(version_pk, str(exc) or exc.__class__.__name__)
            return
        countdown = get_exponential_backoff_interval(
            factor=SCAN_RETRY_BACKOFF_SECONDS,
            retries=self.request.retries,
            maximum=SCAN_RETRY_BACKOFF_MAX_SECONDS,
            full_jitter=True,
        )
        logger.warning("documents.scan.retry", version=version_pk, retries=self.request.retries)
        raise self.retry(exc=exc, countdown=countdown) from exc
    except FileNotFoundError:
        _mark_error(version_pk, "file missing from storage")
        return
    if result.clean:
        _conclude_clean(version_pk, key)
    else:
        _conclude_infected(version_pk, key, result.signature)


@shared_task
def broadcast_document(document_pk: int) -> None:
    """Notify the community's members at levels ``all`` and ``highlights`` that a document was
    published (``document_published``), by batches of ``BROADCAST_BATCH_SIZE``.

    Only members who can open the document hear of it (a restricted document reaches members
    with its ``min_role``); its owner is not notified.
    """
    document = (
        Document.objects.select_related("community", "owner", "current_version")
        .filter(pk=document_pk, status=Document.Status.ACTIVE)
        .first()
    )
    if document is None or document.current_version is None:
        return
    if not document.current_version.is_clean:
        return
    memberships = (
        CommunityMembership.objects.filter(
            community=document.community, notification_level__in=PUBLISHED_LEVELS
        )
        .exclude(user_id=document.owner_id)
        .select_related("user")
        .prefetch_related("user__groups")
        .order_by("user_id")
    )
    last_user_id = 0
    while True:
        batch = list(memberships.filter(user_id__gt=last_user_id)[:BROADCAST_BATCH_SIZE])
        if not batch:
            return
        readers = []
        for membership in batch:
            user = membership.user
            # The membership is known: prime the per-user cache the policies read.
            setattr(user, MEMBERSHIP_CACHE_ATTR, {document.community_id: membership})
            if policies.can_view_document(user, document):
                readers.append(user)
        if readers:
            with transaction.atomic():
                notify(
                    "document_published",
                    readers,
                    actor=document.owner,
                    target=document,
                    community=document.community,
                )
        last_user_id = batch[-1].user_id


# --- lifecycle --------------------------------------------------------------------------


@shared_task
def expire_documents() -> int:
    """Daily: active documents whose ``expires_at`` has passed become ``expired`` (audited
    ``document.expire`` without actor). Returns the number of documents expired."""
    now = timezone.now()
    due = Document.objects.filter(
        status=Document.Status.ACTIVE, expires_at__isnull=False, expires_at__lte=now
    ).values_list("pk", flat=True)
    expired = 0
    for pk in list(due.iterator(chunk_size=500)):
        with transaction.atomic():
            document = (
                Document.objects.select_for_update()
                .filter(pk=pk, status=Document.Status.ACTIVE, expires_at__lte=now)
                .first()
            )
            if document is None:
                continue
            document.status = Document.Status.EXPIRED
            document.save(update_fields=["status", "updated_at"])
            record(
                actor=None,
                action="document.expire",
                target=document,
                changes={
                    "status": {"before": Document.Status.ACTIVE, "after": document.status},
                    "expires_at": document.expires_at.isoformat(),
                },
                community_id=document.community_id,
            )
            expired += 1
    if expired:
        logger.info("documents.expired", count=expired)
    return expired


@shared_task
def purge_download_logs() -> int:
    """Daily: delete the ``DownloadLog`` rows older than ``DOWNLOAD_LOG_RETENTION_DAYS``."""
    cutoff = timezone.now() - timedelta(days=settings.DOWNLOAD_LOG_RETENTION_DAYS)
    deleted = 0
    while True:
        ids = list(
            DownloadLog.objects.filter(created_at__lt=cutoff).values_list("pk", flat=True)[
                :PURGE_BATCH_SIZE
            ]
        )
        if not ids:
            break
        deleted += DownloadLog.objects.filter(pk__in=ids).delete()[0]
    if deleted:
        logger.info("documents.download_logs.purged", count=deleted)
    return deleted


@shared_task
def verify_download_counters() -> int:
    """Nightly: fix drifted ``download_count`` values; returns the number corrected.

    The bulk totals only *detect* a possible drift; each suspect is recounted from the
    current rows (``_recount``), as ``posts.tasks.fix_counters`` does.
    """
    totals = dict(
        DownloadLog.objects.filter(is_preview=False)
        .values("document_id")
        .annotate(total=Count("pk"))
        .order_by()
        .values_list("document_id", "total")
    )
    fixed = 0
    stored = Document.objects.values_list("pk", "download_count")
    for pk, count in stored.iterator(chunk_size=2000):
        if count != totals.get(pk, 0):
            fixed += _recount(pk)
    if fixed:
        logger.info("documents.download_counters.fixed", count=fixed)
    return fixed
