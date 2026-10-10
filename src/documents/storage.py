"""Object keys of document files in the private store (``default_storage``).

A version is uploaded under ``quarantine/<hex>`` (a random key: never the original name),
then moved to ``documents/<same hex>`` once the antivirus finds it clean (``promote``). The
helpers only use the portable ``Storage`` API, so they work with ``InMemoryStorage`` (tests),
``FileSystemStorage`` and ``storages.backends.s3.S3Storage``.
"""

import uuid

import structlog
from django.core.files.storage import default_storage

logger = structlog.get_logger(__name__)

QUARANTINE_PREFIX = "quarantine/"
DOCUMENTS_PREFIX = "documents/"


def new_quarantine_key() -> str:
    return f"{QUARANTINE_PREFIX}{uuid.uuid4().hex}"


def promoted_key(key: str) -> str:
    """The ``documents/`` key matching the quarantine ``key`` (same random part)."""
    return DOCUMENTS_PREFIX + key.rpartition("/")[2]


def save_to_quarantine(upload) -> str:
    """Stream ``upload`` to a new quarantine key and return the key actually stored."""
    if hasattr(upload, "seek"):
        upload.seek(0)
    return default_storage.save(new_quarantine_key(), upload)


def promote(key: str) -> str:
    """Move the object ``key`` under ``documents/`` (copy, then delete) and return its new key.

    Safe to run again after a partial failure: an object already under ``documents/`` is left
    alone, and a copy that exists while the source is gone counts as done.
    """
    if key.startswith(DOCUMENTS_PREFIX):
        return key
    target = promoted_key(key)
    source_exists = default_storage.exists(key)
    if default_storage.exists(target):
        if source_exists:
            delete(key)
        return target
    with default_storage.open(key, "rb") as source:
        saved = default_storage.save(target, source)
    delete(key)
    return saved


def delete(key: str) -> None:
    """Delete the object ``key`` (nothing happens for an empty or missing key)."""
    if key:
        default_storage.delete(key)


def discard(key: str) -> None:
    """``delete`` that never raises (cleanup after another failure); logs a failure."""
    try:
        delete(key)
    except Exception:
        logger.warning("documents.storage.delete_failed", key=key, exc_info=True)


def open_stream(key: str):
    """Open the object ``key`` for reading in binary mode (a file object to close)."""
    return default_storage.open(key, "rb")
