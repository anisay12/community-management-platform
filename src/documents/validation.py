"""Synchronous checks on an uploaded file before it is stored (spec § 8, upload flow step 2).

``validate_upload`` streams the file once in chunks (never reading it whole into memory) to
compute its size and SHA-256 and to keep its first bytes, from which ``python-magic`` detects
the content type. The extension must be in ``ALLOWED_TYPES`` and the detected type must be one
of those accepted for it. Refusals raise ``core.errors.DomainError`` with a translated message.

Decisions where the spec is silent:

- Text formats (txt, md, csv, py, sql, yaml, json, ipynb) accept any ``text/*`` type libmagic
  reports (it names them ``text/plain``, ``text/csv``, ``text/x-script.python``...) and
  ``application/json``, except markup that a browser could render as active content
  (``text/html``, ``text/xml``, SVG): a ``.txt`` file that is really HTML is refused.
- OOXML and OpenDocument files may be reported either as their specific type or as
  ``application/zip`` depending on the libmagic version; both are accepted, and the stored
  ``mime_type`` is then the canonical type of the extension (not the generic container type).
- The sanitized file name is capped at ``FILENAME_MAX_LENGTH`` characters by shortening the stem
  and always keeps its extension; an empty stem becomes ``file``.
"""

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass

import magic
from django.conf import settings
from django.utils.translation import gettext as _

from core.errors import DomainError

CHUNK_SIZE = 64 * 1024
# Bytes given to libmagic: enough for every signature it needs (incl. OOXML / ODF zips).
MAGIC_HEADER_BYTES = 256 * 1024
FILENAME_MAX_LENGTH = 200

OOXML = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
}
# Generic types libmagic may report for a zip-based office document.
ZIP_CONTAINERS = frozenset({"application/zip", "application/x-zip-compressed"})
TEXT = "text"  # marker: any textual type (see ``_is_text``)

# Extension -> accepted detected types. SVG, HTML and executables are deliberately absent.
ALLOWED_TYPES: dict[str, frozenset | str] = {
    "pdf": frozenset({"application/pdf"}),
    **{ext: frozenset({mime}) | ZIP_CONTAINERS for ext, mime in OOXML.items()},
    "png": frozenset({"image/png"}),
    "jpg": frozenset({"image/jpeg"}),
    "jpeg": frozenset({"image/jpeg"}),
    "gif": frozenset({"image/gif"}),
    "webp": frozenset({"image/webp"}),
    "zip": ZIP_CONTAINERS,
    "txt": TEXT,
    "md": TEXT,
    "csv": TEXT,
    "py": TEXT,
    "sql": TEXT,
    "yaml": TEXT,
    "yml": TEXT,
    "json": TEXT,
    "ipynb": TEXT,
}
ALLOWED_EXTENSIONS = frozenset(ALLOWED_TYPES)
# Textual types refused whatever the extension: a browser could run them as active content.
ACTIVE_TEXT_TYPES = frozenset({"text/html", "text/xml", "application/xml", "image/svg+xml"})
TEXT_APPLICATION_TYPES = frozenset({"application/json", "application/x-ndjson"})

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str  # sanitized, keeps the extension
    extension: str  # lower case, without the dot
    mime_type: str  # detected on the content
    size: int
    sha256: str


def sanitize_filename(name: str) -> str:
    """Base name of ``name`` without path, control or reserved characters, length-capped.

    ``../../etc/passwd.pdf`` and ``..\\..\\x.pdf`` keep only ``passwd.pdf`` and ``x.pdf``.
    """
    name = unicodedata.normalize("NFC", str(name or ""))
    name = re.split(r"[/\\]", name)[-1]
    name = "".join(ch for ch in name if not unicodedata.category(ch).startswith("C"))
    name = _UNSAFE_CHARS.sub("_", name)
    name = _SPACES.sub(" ", name).strip().lstrip(".").strip()
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    stem = stem.rstrip(". ") or "file"
    suffix = f".{extension}" if extension else ""
    if len(suffix) > FILENAME_MAX_LENGTH // 2:  # a pathological "extension": not one
        stem, suffix = (stem + suffix), ""
    stem = stem[: FILENAME_MAX_LENGTH - len(suffix)].rstrip(". ") or "file"
    return f"{stem}{suffix}"


def _extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lstrip(".").lower()


def _is_text(mime: str) -> bool:
    if mime in ACTIVE_TEXT_TYPES:
        return False
    return mime.startswith("text/") or mime in TEXT_APPLICATION_TYPES


def _type_matches(extension: str, mime: str) -> bool:
    accepted = ALLOWED_TYPES[extension]
    if accepted == TEXT:
        return _is_text(mime)
    return mime in accepted


def _stored_type(extension: str, mime: str) -> str:
    if extension in OOXML and mime in ZIP_CONTAINERS:
        return OOXML[extension]
    return mime


def _stream(upload) -> tuple[int, str, bytes]:
    """Size, SHA-256 and first bytes of ``upload``, read chunk by chunk from the start."""
    digest = hashlib.sha256()
    size = 0
    header = bytearray()
    if hasattr(upload, "seek"):
        upload.seek(0)
    for chunk in upload.chunks(CHUNK_SIZE):
        digest.update(chunk)
        size += len(chunk)
        if len(header) < MAGIC_HEADER_BYTES:
            header.extend(chunk[: MAGIC_HEADER_BYTES - len(header)])
        if size > settings.DOCUMENT_MAX_UPLOAD_BYTES:
            break  # declared size lied: stop reading, refused below
    if hasattr(upload, "seek"):
        upload.seek(0)
    return size, digest.hexdigest(), bytes(header)


def _too_large() -> DomainError:
    limit_mb = settings.DOCUMENT_MAX_UPLOAD_BYTES / (1024 * 1024)
    return DomainError(
        "file_too_large",
        _("The file is too large (at most %(limit)s MB).") % {"limit": f"{limit_mb:g}"},
    )


def validate_upload(upload) -> ValidatedUpload:
    """Check ``upload`` (a Django ``UploadedFile`` or any ``File``) and describe it.

    Raises ``DomainError``: ``file_empty``, ``file_too_large``, ``extension_refused`` or
    ``type_mismatch``. The file is left positioned at its start.
    """
    filename = sanitize_filename(getattr(upload, "name", ""))
    extension = _extension(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise DomainError(
            "extension_refused",
            _("This type of file is not accepted. Accepted extensions: %(extensions)s.")
            % {"extensions": ", ".join(sorted(ALLOWED_EXTENSIONS))},
        )
    declared = getattr(upload, "size", None)
    if declared is not None and declared > settings.DOCUMENT_MAX_UPLOAD_BYTES:
        raise _too_large()
    size, sha256, header = _stream(upload)
    if size == 0:
        raise DomainError("file_empty", _("The file is empty."))
    if size > settings.DOCUMENT_MAX_UPLOAD_BYTES:
        raise _too_large()
    mime = magic.from_buffer(header, mime=True) or "application/octet-stream"
    if not _type_matches(extension, mime):
        raise DomainError(
            "type_mismatch",
            _("The content of the file does not match its extension “.%(extension)s”.")
            % {"extension": extension},
        )
    return ValidatedUpload(
        filename=filename,
        extension=extension,
        mime_type=_stored_type(extension, mime),
        size=size,
        sha256=sha256,
    )
