"""Deferred work on posts: counters and broadcasts.

Counters (``comment_count`` of posts, ``reaction_counts`` of posts and comments) are not
updated in the business transaction: services call ``schedule_recount`` which, once the
transaction commits, enqueues one ``recount_target`` per target and lock window. The task
releases the lock before counting, so a change committed after it started schedules a new run;
``verify_counters`` repairs any drift every night.
"""

import structlog
from celery import shared_task
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q

from communities.models import CommunityMembership
from notifications.services import notify

from .models import Comment, Post, Reaction

logger = structlog.get_logger(__name__)

RECOUNT_DELAY_SECONDS = 3
RECOUNT_LOCK_SECONDS = 10
BROADCAST_BATCH_SIZE = 500

Level = CommunityMembership.NotificationLevel
# Decision 16: who hears about a newly published post, by notification level.
BROADCAST_LEVELS = {
    "community_post": (Level.ALL,),
    "announcement": (Level.ALL, Level.HIGHLIGHTS),
}

_MODELS = {"posts.post": Post, "posts.comment": Comment}


def _lock_key(model_label: str, pk: int) -> str:
    return f"recount:{model_label}:{pk}"


def _comment_count(post_pk: int) -> int:
    return Comment.objects.filter(post_id=post_pk, status=Comment.Status.VISIBLE).count()


def _reaction_counts(field: str, pk: int) -> dict:
    rows = (
        Reaction.objects.filter(**{f"{field}_id": pk})
        .values("kind")
        .annotate(total=Count("pk"))
        .order_by()
    )
    return {row["kind"]: row["total"] for row in rows}


@shared_task
def recount_target(model_label: str, pk: int) -> None:
    """Recompute ``comment_count`` / ``reaction_counts`` of one post or comment."""
    model = _MODELS.get(model_label)
    if model is None:
        logger.warning("posts.recount.unknown_model", model=model_label)
        return
    try:
        cache.delete(_lock_key(model_label, pk))
    except Exception:  # the lock only deduplicates; counting must still happen
        logger.warning("posts.recount.cache_unavailable", exc_info=True)
    _recount(model, pk)


def _recount(model, pk: int) -> int:
    """Store the current counters of one post or comment; returns 1 when they changed."""
    if model is Post:
        comments, reactions = _comment_count(pk), _reaction_counts("post", pk)
        return (
            Post.objects.filter(pk=pk)
            .exclude(comment_count=comments, reaction_counts=reactions)
            .update(comment_count=comments, reaction_counts=reactions)
        )
    reactions = _reaction_counts("comment", pk)
    return (
        Comment.objects.filter(pk=pk)
        .exclude(reaction_counts=reactions)
        .update(reaction_counts=reactions)
    )


def _enqueue(model_label: str, pk: int) -> None:
    key = _lock_key(model_label, pk)
    try:
        if not cache.add(key, 1, RECOUNT_LOCK_SECONDS):
            return  # a recount is already queued and has not started yet
    except Exception:  # cache down: enqueue anyway (duplicates are harmless)
        logger.warning("posts.recount.cache_unavailable", exc_info=True)
    try:
        recount_target.apply_async((model_label, pk), countdown=RECOUNT_DELAY_SECONDS)
    except Exception:  # broker down: verify_counters fixes the drift tonight
        logger.warning("posts.recount.enqueue_failed", model=model_label, pk=pk, exc_info=True)
        try:
            cache.delete(key)
        except Exception:
            logger.warning("posts.recount.cache_unavailable", exc_info=True)


def schedule_recount(obj) -> None:
    """Schedule ``recount_target`` for ``obj`` on commit (deduplicated)."""
    model_label, pk = obj._meta.label_lower, obj.pk
    transaction.on_commit(lambda: _enqueue(model_label, pk))


def _reaction_totals(field: str) -> dict[int, dict]:
    totals: dict[int, dict] = {}
    rows = (
        Reaction.objects.filter(**{f"{field}__isnull": False})
        .values(f"{field}_id", "kind")
        .annotate(total=Count("pk"))
        .order_by()
    )
    for row in rows:
        totals.setdefault(row[f"{field}_id"], {})[row["kind"]] = row["total"]
    return totals


@shared_task
def verify_counters() -> None:
    """Nightly: fix drifted counters."""
    fixed = fix_counters()
    if fixed:
        logger.info("posts.counters.fixed", count=fixed)


def fix_counters() -> int:
    """Correct every drifted counter; returns the number of corrected posts and comments.

    The bulk totals and the stored counters are read at different moments, so they only
    *detect* a possible drift: each suspect is recounted from the current rows
    (``_recount``), never overwritten with the older totals (a reaction recounted while the
    job runs stays counted).
    """
    fixed = 0
    post_reactions = _reaction_totals("post")
    posts = Post.objects.annotate(
        actual_comments=Count("comments", filter=Q(comments__status=Comment.Status.VISIBLE))
    ).only("pk", "comment_count", "reaction_counts")
    for post in posts.iterator(chunk_size=2000):
        reactions = post_reactions.get(post.pk, {})
        if post.comment_count != post.actual_comments or post.reaction_counts != reactions:
            fixed += _recount(Post, post.pk)
    comment_reactions = _reaction_totals("comment")
    comments = Comment.objects.exclude(reaction_counts={}).values_list("pk", "reaction_counts")
    candidates = dict(comments.iterator(chunk_size=2000))
    for pk in comment_reactions.keys() | candidates.keys():
        if candidates.get(pk, {}) != comment_reactions.get(pk, {}):
            fixed += _recount(Comment, pk)
    return fixed


@shared_task
def broadcast_post(post_id: int, category: str) -> None:
    """Notify the members of the post's community according to their notification level.

    ``community_post`` reaches members at level ``all``; ``announcement`` also those at
    ``highlights``. The author is never notified. Notifications are stored by batches of
    ``BROADCAST_BATCH_SIZE`` recipients.
    """
    levels = BROADCAST_LEVELS.get(category)
    if levels is None:
        logger.warning("posts.broadcast.unknown_category", category=category)
        return
    post = (
        Post.objects.select_related("community", "author")
        .filter(pk=post_id, status=Post.Status.PUBLISHED)
        .first()
    )
    if post is None:
        return
    memberships = (
        CommunityMembership.objects.filter(community=post.community, notification_level__in=levels)
        .exclude(user_id=post.author_id)
        .select_related("user")
        .order_by("user_id")
    )
    last_user_id = 0
    while True:
        batch = [
            m.user for m in memberships.filter(user_id__gt=last_user_id)[:BROADCAST_BATCH_SIZE]
        ]
        if not batch:
            return
        with transaction.atomic():
            notify(category, batch, actor=post.author, target=post, community=post.community)
        last_user_id = batch[-1].pk
