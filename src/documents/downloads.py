"""Private download of document versions (spec § 8 "Download", ADR-0001).

The view checks the access right on every request, records the download here, then answers
an empty response whose ``X-Accel-Redirect`` header points Nginx at the internal location
``settings.DOCUMENT_PROTECTED_PREFIX``. Nginx relays the read to the private object store and
streams the file; the browser never sees a storage URL.

Nginx authenticates to the storage with a short-lived presigned URL generated here: the
internal redirect carries its path and query (``/_protected/<bucket>/<key>?X-Amz-...``), and
the ``/_protected/`` location proxies them to the storage endpoint the URL was signed for
(``STORAGE_ORIGIN`` in ``docker/nginx/default.conf``). This works with any backend whose
``url()`` signs (S3, SeaweedFS, MinIO, Azure SAS); a backend without signed URLs (the
in-memory test storage) falls back to ``/_protected/<storage_key>``.
"""

from urllib.parse import quote, urlsplit

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils.translation import gettext as _

from core.errors import DomainError

from . import policies
from .models import DownloadLog
from .tasks import schedule_download_recount

# Validity of the presigned URL handed to Nginx: it is used at once, server side, and never
# leaves the internal network, so a few seconds of clock skew are the only margin needed.
PRESIGNED_URL_TTL_SECONDS = 60


@transaction.atomic
def record_download(*, user, document, version, preview=False) -> DownloadLog:
    """Log one download (or preview) of ``version`` by ``user`` and schedule the recount of
    ``document.download_count`` (previews are logged but not counted).

    Raises ``DomainError("forbidden")`` when ``policies.can_download`` refuses it.
    """
    if not policies.can_download(user, document, version):
        raise DomainError("forbidden", _("You cannot download this document."))
    log = DownloadLog.objects.create(
        document=document, version=version, user=user, is_preview=preview
    )
    if not preview:
        schedule_download_recount(document)
    return log


def _presigned_url(storage_key: str) -> str | None:
    """A short-lived signed URL of ``storage_key``, or ``None`` when the default storage
    cannot sign (no ``expire`` argument, or a relative unsigned URL)."""
    try:
        url = default_storage.url(storage_key, expire=PRESIGNED_URL_TTL_SECONDS)
    except (TypeError, NotImplementedError, ValueError):
        return None
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return url


def protected_location(version) -> str:
    """The ``X-Accel-Redirect`` target of ``version``: the internal prefix followed by the
    path and query of a presigned storage URL, or by the storage key as a fallback."""
    prefix = settings.DOCUMENT_PROTECTED_PREFIX
    url = _presigned_url(version.storage_key)
    if url is None:
        return prefix + quote(version.storage_key)
    parts = urlsplit(url)
    location = prefix + parts.path.lstrip("/")
    return f"{location}?{parts.query}" if parts.query else location
