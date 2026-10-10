"""Deferred work on documents.

``download_count`` is a deferred counter (spec § 2): the download view records a
``DownloadLog`` row and calls ``schedule_download_recount``, which enqueues one
``recount_downloads`` per document and lock window once the transaction commits.
"""

import structlog
from celery import shared_task
from django.core.cache import cache
from django.db import transaction

from .models import Document, DownloadLog

logger = structlog.get_logger(__name__)

RECOUNT_DELAY_SECONDS = 3
RECOUNT_LOCK_SECONDS = 10


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
