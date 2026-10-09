import unicodedata
import uuid

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils.text import slugify
from django.utils.translation import gettext as _

from audit.services import record
from core.errors import DomainError

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


def _move_links(through, owner_field: str, source: Tag, target: Tag) -> int:
    """Point the ``source`` rows of an M2M ``through`` table at ``target`` (rows whose owner
    already has ``target`` are dropped instead); the number of owners concerned."""
    rows = through.objects.filter(tag=source)
    owners = rows.count()
    already = through.objects.filter(tag=target).values(owner_field)
    rows.filter(**{f"{owner_field}__in": already}).delete()
    rows.update(tag=target)
    return owners


@transaction.atomic
def merge_tags(*, actor, source: Tag, target: Tag) -> Tag:
    """Merge ``source`` into ``target`` (functional administrators): every post and community
    tagged ``source`` gets ``target`` (never twice), then ``source`` is deleted.

    ``target`` keeps its name and key. Affected posts get their search vector refreshed and
    their ``version`` bumped, so an editor opened on the old tags answers ``edit_conflict``
    instead of recreating the merged tag. Audited ``tag.merged``.
    """
    from communities.models import Community
    from posts import policies as post_policies
    from posts.models import Post

    # Same title/tags/body vector as the post services (posts depends on taxonomy, not the
    # reverse, hence the local imports).
    from posts.services_posts import _refresh_search_vector

    if not post_policies.can_merge_tags(actor):
        raise DomainError("forbidden", _("You are not allowed to perform this action."))
    if source.pk == target.pk:
        raise DomainError("same_tag", _("Choose two different tags."))
    # Lock both tags in a stable order so concurrent merges serialise.
    locked = {
        tag.pk: tag
        for tag in Tag.objects.select_for_update()
        .filter(pk__in=[source.pk, target.pk])
        .order_by("pk")
    }
    if len(locked) != 2:
        raise DomainError("invalid_state", _("This action is not possible in the current state."))
    source, target = locked[source.pk], locked[target.pk]
    post_ids = list(Post.tags.through.objects.filter(tag=source).values_list("post_id", flat=True))
    posts = _move_links(Post.tags.through, "post_id", source, target)
    communities = _move_links(Community.tags.through, "community_id", source, target)
    Post.objects.filter(pk__in=post_ids).update(version=F("version") + 1)
    for post in Post.objects.filter(pk__in=post_ids):
        _refresh_search_vector(post)
    record(
        actor=actor,
        action="tag.merged",
        target=target,
        changes={
            "source": source.name,
            "source_key": source.key,
            "target": target.name,
            "posts": posts,
            "communities": communities,
        },
    )
    source.delete()
    return target
