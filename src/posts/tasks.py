"""Deferred work on posts: counters and broadcasts.

Task 1 ships these as no-op stubs so services can schedule them; Task 3 implements them.
"""

from celery import shared_task


@shared_task
def recount_target(model_label: str, pk: int) -> None:
    """Recompute ``comment_count`` / ``reaction_counts`` of one post or comment."""
    return None


def schedule_recount(obj) -> None:
    """Schedule ``recount_target`` for ``obj`` on commit (deduplicated)."""
    return None


@shared_task
def verify_counters() -> None:
    """Nightly: fix drifted counters."""
    return None


@shared_task
def broadcast_post(post_id: int, category: str) -> None:
    """Notify the members of the post's community according to their notification level."""
    return None
