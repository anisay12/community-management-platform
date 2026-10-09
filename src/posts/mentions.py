"""``@first.last`` mentions: handles, extraction, resolution against members, storage."""

import re
import unicodedata

from django.db import transaction
from django.utils.text import slugify

from accounts.models import User
from communities.policies import can_view_content

from .models import Comment, Mention

# ``@`` not preceded by a word character, a dot or another ``@`` (so emails do not match).
HANDLE_RE = re.compile(r"(?<![\w@.])@([a-z0-9]+(?:-[a-z0-9]+)*\.[a-z0-9]+(?:-[a-z0-9]+)*)", re.I)


def _handle_part(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    return slugify(ascii_only)


def handle_for(user) -> str:
    """``first.last``: accent-free, lowercase, spaces as hyphens (``Jean Rémi`` → ``jean-remi``)."""
    return f"{_handle_part(user.first_name)}.{_handle_part(user.last_name)}"


def extract_handles(text: str) -> set[str]:
    return {match.lower() for match in HANDLE_RE.findall(text or "")}


def resolve_mentions(community, text: str) -> list:
    """Members of ``community`` mentioned in ``text``.

    A handle resolves only when exactly one active member has it and that member can read
    the community's content; other handles stay plain text.
    """
    handles = extract_handles(text)
    if not handles:
        return []
    by_handle: dict[str, list] = {}
    members = User.objects.filter(
        community_memberships__community=community, status=User.Status.ACTIVE
    ).order_by("pk")
    for member in members:
        handle = handle_for(member)
        if handle in handles:
            by_handle.setdefault(handle, []).append(member)
    return [
        found[0]
        for _handle, found in sorted(by_handle.items())
        if len(found) == 1 and can_view_content(found[0], community)
    ]


@transaction.atomic
def sync_mentions(source, users) -> list:
    """Store the mentions of ``source`` (a post or comment); returns the newly mentioned users.

    Mentions no longer present in the text are removed.
    """
    field = "comment" if isinstance(source, Comment) else "post"
    wanted = {user.pk: user for user in users}
    existing = set(
        Mention.objects.filter(**{field: source}).values_list("mentioned_user_id", flat=True)
    )
    Mention.objects.filter(**{field: source}).exclude(mentioned_user_id__in=wanted).delete()
    new_users = [user for pk, user in wanted.items() if pk not in existing]
    Mention.objects.bulk_create(
        [Mention(**{field: source}, mentioned_user=user) for user in new_users]
    )
    return new_users
