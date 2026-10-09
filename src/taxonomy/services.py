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


def get_or_create_tag(name: str) -> Tag:
    """Return the tag named ``name`` (case-insensitively), creating it when missing."""
    existing = Tag.objects.filter(name__iexact=name).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return Tag.objects.create(name=name, slug=_unique_slug(name))
    except IntegrityError:
        # Created concurrently (same name or slug): reuse the winner or retry the slug.
        existing = Tag.objects.filter(name__iexact=name).first()
        if existing is not None:
            return existing
        return Tag.objects.create(name=name, slug=f"{_unique_slug(name)}-{uuid.uuid4().hex[:8]}")
