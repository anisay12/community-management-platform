"""Profile photos (spec § 4 L1, delivered in L5): checks, re-encoding and storage keys.

Flow: ``services.upload_avatar`` checks the upload synchronously (``check_upload``: size,
type detected on content, readable image header, pixel cap), stores the original bytes in
quarantine (``documents.storage``) and queues ``tasks.scan_avatar``. Only after a clean
ClamAV verdict does the task decode the image fully and re-encode it (``reencode``) under
``avatars/<random hex>.webp``, which becomes ``UserProfile.avatar``.

Decisions where the spec is silent:

- Pending state: two fields on ``UserProfile`` (``avatar_pending_key``, ``avatar_scan_status``)
  rather than a model: one photo per person, one upload in flight. A new upload replaces a
  pending one (the older quarantine object is deleted; the older task finds the key changed
  and stops). ``infected`` / ``error`` stay visible to the owner until the next upload or
  removal.
- Output: WebP (lossy, quality 85), at most ``AVATAR_MAX_SIDE`` pixels per side, alpha kept,
  no metadata (EXIF, ICC, XMP are not copied). The EXIF orientation is applied first so that
  a phone photo is not shown rotated once its metadata is gone.
- Decompression bombs: the header's width x height must not exceed ``AVATAR_MAX_PIXELS``
  (checked before any decoding, both at upload and before re-encoding), and Pillow's own
  ``DecompressionBombError`` / warning is treated as a refusal.
"""

import io
import uuid
import warnings

import magic
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

from core.errors import DomainError

AVATAR_PREFIX = "avatars/"
AVATAR_MAX_SIDE = 512
ALLOWED_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
PILLOW_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
OUTPUT_MIME_TYPE = "image/webp"
MAGIC_HEADER_BYTES = 8 * 1024


def _invalid_image() -> DomainError:
    return DomainError(
        "invalid_image", _("This image cannot be read. Use a PNG, JPEG or WebP photo.")
    )


def _open_checked(fileobj) -> Image.Image:
    """Open ``fileobj`` lazily (header only) and refuse unknown formats and oversized images."""
    fileobj.seek(0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(fileobj)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise _too_many_pixels() from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise _invalid_image() from exc
    if image.format not in PILLOW_FORMATS:
        raise _invalid_image()
    width, height = image.size
    if width < 1 or height < 1:
        raise _invalid_image()
    if width * height > settings.AVATAR_MAX_PIXELS:
        raise _too_many_pixels()
    return image


def _too_many_pixels() -> DomainError:
    return DomainError("image_too_large", _("This image has too many pixels. Use a smaller photo."))


def check_upload(upload) -> None:
    """Refuse an upload that is too big, not PNG/JPEG/WebP by content, or not a readable
    image within the pixel cap. Raises ``DomainError`` with a translated message."""
    if upload.size > settings.AVATAR_MAX_UPLOAD_BYTES:
        limit = settings.AVATAR_MAX_UPLOAD_BYTES // (1024 * 1024)
        raise DomainError(
            "file_too_large",
            _("The photo must not exceed %(size)s MB.") % {"size": limit},
        )
    upload.seek(0)
    detected = magic.from_buffer(upload.read(MAGIC_HEADER_BYTES), mime=True)
    if detected not in ALLOWED_MIME_TYPES:
        raise DomainError("invalid_type", _("Only PNG, JPEG and WebP photos are accepted."))
    _open_checked(upload)
    upload.seek(0)


def reencode(fileobj) -> bytes:
    """Decode ``fileobj`` fully and return a metadata-free WebP of at most
    ``AVATAR_MAX_SIDE`` px per side. Raises ``DomainError`` for an unreadable image."""
    image = _open_checked(fileobj)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image.load()
        image = ImageOps.exif_transpose(image)
        image.thumbnail((AVATAR_MAX_SIDE, AVATAR_MAX_SIDE))
        mode = (
            "RGBA" if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info else "RGB"
        )
        clean = Image.new(mode, image.size)
        clean.paste(image.convert(mode))
        output = io.BytesIO()
        clean.save(output, format="WEBP", quality=85)
    except DomainError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise _too_many_pixels() from exc
    except (OSError, ValueError, SyntaxError) as exc:
        raise _invalid_image() from exc
    return output.getvalue()


def save_clean(content: bytes) -> str:
    """Store a re-encoded photo under a new random key and return the key actually stored."""
    return default_storage.save(f"{AVATAR_PREFIX}{uuid.uuid4().hex}.webp", ContentFile(content))


def version_token(name: str) -> str:
    """A short cache-busting token derived from the stored key (changes on every upload)."""
    return name.rpartition("/")[2].partition(".")[0][:12]
