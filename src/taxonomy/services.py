import unicodedata
import uuid

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from .models import Tag

SLUG_BASE_LENGTH = 40


def _unique_slug(name: str) -> str:
    base = slugify(name)[:SLUG_BASE_LENGTH].strip("-") or "tag"
    if not Tag.objects.filter(slug=base).exists():
        return base
    return f"{base}-{uuid.uuid4().hex[:8]}"


def normalize_tag_key(name: str) -> str:
    """The lookup key of a tag name: lowercase, without accents, single-spaced."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.lower().split())[: Tag._meta.get_field("key").max_length]


def get_or_create_tag(name: str) -> Tag:
    """Return the tag whose key matches ``name`` (case and accents ignored), creating it."""
    name = " ".join(name.split())
    existing = Tag.objects.filter(key=normalize_tag_key(name)).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return Tag.objects.create(name=name, slug=_unique_slug(name))
    except IntegrityError:
        # Created concurrently (same name or slug): reuse the winner or retry the slug.
        existing = Tag.objects.filter(key=normalize_tag_key(name)).first()
        if existing is not None:
            return existing
        return Tag.objects.create(name=name, slug=f"{_unique_slug(name)}-{uuid.uuid4().hex[:8]}")
