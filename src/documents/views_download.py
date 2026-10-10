"""Download and preview of document versions (spec § 8 "Download", ADR-0001).

- Document the user may not open, unknown version number, version the user may not see
  (``policies.can_view_version``), or infected version (for everyone) → 404.
- Preview of a type other than PDF and raster images → 404 (those are download only).
- Visible version whose download is refused (``policies.can_download``) → 403. A manager
  asking for a version still being scanned (or whose scan failed) also gets the 403 page,
  with a toast explaining why: the version exists and they may see it, but no file is ever
  served before a clean scan.

Production (``DEBUG`` false): empty response with ``X-Accel-Redirect`` and the headers set
here, which Nginx keeps while it streams the file from the private storage. Development
(``DEBUG`` true and ``DOCUMENT_DEV_STREAMING``): the file is streamed by Django itself.
"""

import unicodedata
from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET

from core.errors import DomainError

from . import downloads, policies, selectors
from .models import DocumentVersion

# Types shown inline by the preview (spec § 8: PDF and images at the MVP; SVG is refused at
# upload). Every other type is download only.
PREVIEW_MIME_TYPES = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}
)
PREVIEW_CSP = "sandbox; default-src 'none'"
FALLBACK_FILENAME = "download"


def _ascii_filename(filename: str) -> str:
    """An ASCII rendering of ``filename`` for the legacy ``filename=`` parameter: accents
    dropped, quotes, backslashes and control characters replaced."""
    decomposed = unicodedata.normalize("NFKD", filename)
    ascii_name = decomposed.encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(
        "_" if char in '"\\' or ord(char) < 0x20 or ord(char) == 0x7F else char
        for char in ascii_name
    ).strip()
    return cleaned or FALLBACK_FILENAME


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """``attachment`` (or ``inline``) with an ASCII ``filename`` fallback and the exact name
    as RFC 5987/6266 ``filename*``."""
    disposition = "inline" if inline else "attachment"
    filename = filename or FALLBACK_FILENAME
    return (
        f'{disposition}; filename="{_ascii_filename(filename)}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def _set_file_headers(response, version, *, preview: bool):
    response["Content-Type"] = version.mime_type
    response["Content-Disposition"] = content_disposition(version.original_filename, inline=preview)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    if preview:
        response["Content-Security-Policy"] = PREVIEW_CSP
    return response


def _dev_streaming() -> bool:
    return bool(settings.DEBUG and settings.DOCUMENT_DEV_STREAMING)


def _file_response(version, *, preview: bool):
    if _dev_streaming():
        response = FileResponse(
            default_storage.open(version.storage_key, "rb"), content_type=version.mime_type
        )
    else:
        response = HttpResponse(content=b"", content_type=version.mime_type)
        response["X-Accel-Redirect"] = downloads.protected_location(version)
    return _set_file_headers(response, version, preview=preview)


def _version_or_404(user, document, number):
    if number is None:
        version = document.current_version
    else:
        version = document.versions.filter(number=number).first()
    if version is None:
        raise Http404
    version.document = document  # keep the community already loaded
    if version.scan_status == DocumentVersion.ScanStatus.INFECTED or not version.storage_key:
        raise Http404
    if not policies.can_view_version(user, version):
        raise Http404
    return version


def _serve(request, public_id, number, *, preview: bool):
    document = selectors.get_visible_document_or_404(request.user, public_id)
    version = _version_or_404(request.user, document, number)
    if preview and version.mime_type not in PREVIEW_MIME_TYPES:
        raise Http404
    if not policies.can_download(request.user, document, version):
        if not version.is_clean:
            messages.warning(
                request,
                _("This version cannot be downloaded until its virus scan has succeeded."),
            )
        raise PermissionDenied
    try:
        downloads.record_download(
            user=request.user, document=document, version=version, preview=preview
        )
    except DomainError as error:  # refused between the check above and the write
        raise PermissionDenied from error
    return _file_response(version, preview=preview)


@login_required
@require_GET
def download(request, public_id, number=None):
    """Download the reference version (or version ``number``) as an attachment."""
    return _serve(request, public_id, number, preview=False)


@login_required
@require_GET
def preview(request, public_id, number=None):
    """Show the reference version (or version ``number``) inline: PDF and images only."""
    return _serve(request, public_id, number, preview=True)
