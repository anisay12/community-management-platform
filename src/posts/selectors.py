"""Read-side queries on posts. Visibility is decided in SQL here and mirrored by
``policies.can_view_post`` (the visibility tests check that they agree)."""

import base64
import binascii
from dataclasses import dataclass, field
from datetime import UTC, datetime

from django.db.models import Exists, OuterRef, Prefetch, Q, prefetch_related_objects
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.roles import Role, has_role
from communities.models import AdminAccessGrant, Community, CommunityMembership
from communities.policies import is_functional_admin
from communities.roles import ROLE_RANK, CommunityRole

from .models import Comment, Post

MODERATOR_ROLES = [
    role for role, rank in ROLE_RANK.items() if rank >= ROLE_RANK[CommunityRole.MODERATOR]
]


def community_moderators(community) -> list:
    """Members of ``community`` with the moderator role or above (recipients of review
    requests and moderation alerts)."""
    return [
        membership.user
        for membership in CommunityMembership.objects.select_related("user").filter(
            community=community, role__in=MODERATOR_ROLES
        )
    ]


def _content_community_q(user, prefix: str = "community__") -> Q:
    """``Q`` on posts whose community content ``user`` may read (``can_view_content`` in SQL)."""
    member_ids = CommunityMembership.objects.filter(user=user).values("community_id")
    open_and_live = Q(**{f"{prefix}access_mode": Community.AccessMode.OPEN}) & ~Q(
        **{f"{prefix}status": Community.Status.ARCHIVED}
    )
    condition = Q(**{f"{prefix}pk__in": member_ids})
    if is_functional_admin(user):
        grant_ids = AdminAccessGrant.objects.filter(
            user=user, expires_at__gt=timezone.now()
        ).values("community_id")
        return condition | open_and_live | Q(**{f"{prefix}pk__in": grant_ids})
    if has_role(user, Role.TECHNICAL_ADMIN) or has_role(user, Role.AUDITOR):
        return condition
    return condition | open_and_live


def visible_posts_q(user) -> Q | None:
    """``Q`` of the posts ``user`` may open, or ``None`` when they may open none.

    Published and archived posts of the communities whose content the user reads; the
    user's own drafts, pending and hidden posts; pending and hidden posts of the communities
    where the user moderates (moderator+, or functional admin with content access).
    """
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    Status = Post.Status
    content = _content_community_q(user)
    moderated_ids = CommunityMembership.objects.filter(user=user, role__in=MODERATOR_ROLES).values(
        "community_id"
    )
    moderated = Q(community__in=moderated_ids)
    if is_functional_admin(user):
        moderated |= content
    return (
        (Q(status__in=[Status.PUBLISHED, Status.ARCHIVED]) & content)
        | Q(author=user, status__in=[Status.DRAFT, Status.PENDING_REVIEW, Status.HIDDEN])
        | (Q(status__in=[Status.PENDING_REVIEW, Status.HIDDEN]) & moderated)
    )


def get_visible_post_or_404(user, community_slug, public_id) -> Post:
    """The post ``public_id`` of community ``community_slug`` if ``user`` may open it."""
    return get_object_or_404(
        Post.objects.visible_to(user).select_related("community", "author"),
        community__slug=community_slug,
        public_id=public_id,
    )


# Feeds -------------------------------------------------------------------------------------

FEED_PAGE_SIZE = 20
MAX_PINNED = 3
_FEED_ORDER = ("-last_activity_at", "-pk")


@dataclass
class FeedPage:
    """One page of a feed: ``pinned`` posts (first page of the unfiltered community feed
    only), then ``items`` by last activity; ``next_cursor`` is ``None`` on the last page."""

    items: list
    next_cursor: str | None
    pinned: list = field(default_factory=list)


def encode_cursor(post) -> str:
    """Opaque cursor positioned right after ``post`` (``last_activity_at|id``, base64)."""
    raw = f"{post.last_activity_at.isoformat()}|{post.pk}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(value) -> tuple[datetime, int] | None:
    """``(last_activity_at, id)`` of a cursor (in UTC), or ``None`` when it is missing or
    malformed, naive, or out of range once converted to UTC (a tampered offset)."""
    try:
        raw, _, pk = base64.urlsafe_b64decode(str(value).encode()).decode().partition("|")
        moment = datetime.fromisoformat(raw)
        if moment.tzinfo is None:
            return None
        return moment.astimezone(UTC), int(pk)
    except (ValueError, UnicodeError, binascii.Error, OverflowError):
        return None


def _feed_queryset(user):
    return Post.objects.visible_to(user).select_related("community", "shared_from__community")


def _is_first_page(cursor) -> bool:
    return not cursor or decode_cursor(cursor) is None


def _page(queryset, cursor) -> FeedPage:
    position = decode_cursor(cursor) if cursor else None
    if position is not None:
        moment, pk = position
        queryset = queryset.filter(
            Q(last_activity_at__lt=moment) | Q(last_activity_at=moment, pk__lt=pk)
        )
    rows = list(queryset.order_by(*_FEED_ORDER)[: FEED_PAGE_SIZE + 1])
    items = rows[:FEED_PAGE_SIZE]
    next_cursor = encode_cursor(items[-1]) if len(rows) > FEED_PAGE_SIZE else None
    return FeedPage(items=items, next_cursor=next_cursor)


def community_feed(user, community, *, kind=None, unanswered=False, cursor=None) -> FeedPage:
    """Posts of ``community`` that ``user`` may open, without drafts or archived posts.

    Pending and hidden posts appear only for their author and the moderators (``visible_to``).
    ``kind`` (an unknown value is ignored) and ``unanswered`` (questions without an accepted
    answer nor any visible comment) filter the feed; the unfiltered feed puts up to three
    pinned posts first on its first page and leaves them out of every page's ``items``.
    """
    queryset = (
        _feed_queryset(user)
        .filter(community=community)
        .exclude(status__in=[Post.Status.DRAFT, Post.Status.ARCHIVED])
    )
    pinned = []
    if unanswered:
        visible_comments = Comment.objects.filter(
            post=OuterRef("pk"), status=Comment.Status.VISIBLE
        )
        queryset = queryset.filter(kind=Post.Kind.QUESTION, accepted_answer__isnull=True).exclude(
            Exists(visible_comments)
        )
    elif kind in Post.Kind.values:
        queryset = queryset.filter(kind=kind)
    else:
        pinned = list(
            queryset.filter(pinned_at__isnull=False).order_by("-pinned_at", "-pk")[:MAX_PINNED]
        )
        queryset = queryset.exclude(pk__in=[post.pk for post in pinned])
    page = _page(queryset, cursor)
    if _is_first_page(cursor):
        page.pinned = pinned
    # One tag query for the pinned posts and the page together.
    prefetch_related_objects([*page.pinned, *page.items], "tags")
    return page


def home_feed(user, *, cursor=None) -> FeedPage:
    """Published posts of the communities ``user`` is a member of, by last activity."""
    if not getattr(user, "is_authenticated", False):
        return FeedPage(items=[], next_cursor=None)
    member_of = CommunityMembership.objects.filter(user=user).values("community_id")
    queryset = _feed_queryset(user).filter(community__in=member_of, status=Post.Status.PUBLISHED)
    page = _page(queryset, cursor)
    prefetch_related_objects(page.items, "tags")
    return page


def mark_share_access(user, posts) -> None:
    """Set ``share_original_visible`` on each post: whether ``user`` may open the post it
    shares (one query for the whole list, rather than a policy check per card)."""
    original_ids = {post.shared_from_id for post in posts if post.shared_from_id}
    visible = (
        set(Post.objects.visible_to(user).filter(pk__in=original_ids).values_list("pk", flat=True))
        if original_ids
        else set()
    )
    for post in posts:
        post.share_original_visible = post.shared_from_id in visible


# Comments ----------------------------------------------------------------------------------


def comment_thread(user, post, *, moderator=None) -> list[Comment]:
    """Top-level comments of ``post`` with their replies in ``prefetched_replies``.

    Hidden comments stay in the thread (as a placeholder): ``body_visible`` is true only for
    visible comments, or for hidden ones when ``user`` is their author or a moderator.
    ``moderator`` is whether ``user`` moderates the community, when the caller already knows.
    """
    if moderator is None:
        from .policies import is_content_moderator

        moderator = is_content_moderator(user, post.community)
    replies = Comment.objects.order_by("created_at", "pk")
    thread = list(
        Comment.objects.filter(post=post, parent__isnull=True)
        .order_by("created_at", "pk")
        .prefetch_related(Prefetch("replies", queryset=replies, to_attr="prefetched_replies"))
    )
    user_pk = getattr(user, "pk", None)
    for comment in thread:
        for item in (comment, *comment.prefetched_replies):
            item.body_visible = (
                item.status == Comment.Status.VISIBLE
                or moderator
                or (item.author_id is not None and item.author_id == user_pk)
            )
    return thread
