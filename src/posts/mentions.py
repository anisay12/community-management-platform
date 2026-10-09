"""``@first.last`` mentions: handles, extraction, resolution against members, storage."""

import re
import unicodedata

from django.db import transaction
from django.db.models import F, Func, Q, Value
from django.utils.text import slugify

from accounts.models import User
from communities.policies import can_view_content

from .models import Comment, Mention

# ``@`` not preceded by a word character, a dot or another ``@`` (so emails do not match).
HANDLE_RE = re.compile(r"(?<![\w@.])@([a-z0-9]+(?:-[a-z0-9]+)*\.[a-z0-9]+(?:-[a-z0-9]+)*)", re.I)


def _handle_part(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    # Underscores separate words like spaces, so every handle matches ``HANDLE_RE``.
    return slugify(ascii_only.replace("_", " "))


def handle_for(user) -> str:
    """``first.last``: accent-free, lowercase, spaces and underscores as hyphens
    (``Jean Rémi`` → ``jean-remi``, ``Jean_Paul`` → ``jean-paul``)."""
    return f"{_handle_part(user.first_name)}.{_handle_part(user.last_name)}"


def _build_fold_table() -> tuple[str, str, str]:
    """``translate()`` arguments mirroring ``_handle_part`` per character for Latin letters.

    Each non-ASCII Latin character (and combining mark) folds to the single ASCII letter or
    digit ``_handle_part`` keeps for it, or is deleted when it keeps none. Characters that fold
    to several letters (``Ĳ``) are left out: a name still holding a non-ASCII character after
    the fold is always a candidate, so the SQL filter never misses a member.
    """
    ranges = ((0x80, 0x250), (0x300, 0x370), (0x1E00, 0x1F00))
    mapped_from, mapped_to, deleted = [], [], []
    for start, end in ranges:
        for code in range(start, end):
            char = chr(code)
            folded = re.sub(r"[^a-z0-9]", "", _handle_part(char))
            if len(folded) == 1:
                mapped_from.append(char)
                mapped_to.append(folded)
            elif not folded:
                deleted.append(char)
    return "".join(mapped_from + deleted), "".join(mapped_to), "[^\x01-\x7f]"


_FOLD_FROM, _FOLD_TO, _NON_ASCII = _build_fold_table()


def _folded(field: str) -> Func:
    return Func(F(field), Value(_FOLD_FROM), Value(_FOLD_TO), function="translate")


def _key(expression) -> Func:
    """Lowercase ASCII letters and digits only: ``Jean-Rémi`` → ``jeanremi``."""
    stripped = Func(
        expression, Value("[^a-zA-Z0-9]+"), Value(""), Value("g"), function="regexp_replace"
    )
    return Func(stripped, function="lower")


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
    firsts, lasts = set(), set()
    for handle in handles:
        first, last = handle.split(".", 1)
        firsts.add(first.replace("-", ""))
        lasts.add(last.replace("-", ""))
    # SQL pre-filter: a superset of the members whose handle appears, confirmed below.
    members = (
        User.objects.filter(community_memberships__community=community, status=User.Status.ACTIVE)
        .alias(
            first_folded=_folded("first_name"),
            last_folded=_folded("last_name"),
            first_key=_key(_folded("first_name")),
            last_key=_key(_folded("last_name")),
        )
        .filter(
            (Q(first_key__in=firsts) | Q(first_folded__regex=_NON_ASCII))
            & (Q(last_key__in=lasts) | Q(last_folded__regex=_NON_ASCII))
        )
        .order_by("pk")
    )
    by_handle: dict[str, list] = {}
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
