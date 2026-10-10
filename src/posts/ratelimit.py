"""Per-user hourly rate limits for writes (fixed one-hour windows in the cache)."""

import time

import structlog
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext as _

from core.errors import DomainError

logger = structlog.get_logger(__name__)

WINDOW_SECONDS = 3600


def hit(user, bucket: str) -> None:
    """Count one ``bucket`` action of ``user``; raise ``rate_limited`` past the hourly limit.

    The error carries ``retry_after`` (seconds to the next window). When the cache is
    unavailable the check is skipped (fail open) and logged.
    """
    limit = settings.POSTS_RATE_LIMITS[bucket]
    now = time.time()
    window = int(now // WINDOW_SECONDS)
    key = f"rl:{bucket}:{user.pk}:{window}"
    try:
        cache.add(key, 0, timeout=WINDOW_SECONDS + 60)
        count = cache.incr(key)
    except Exception:  # cache backend down: never block writes on it
        logger.warning("posts.ratelimit.unavailable", bucket=bucket, exc_info=True)
        return
    if count > limit:
        error = DomainError(
            "rate_limited", _("You are doing this too often. Please try again later.")
        )
        error.retry_after = max(1, (window + 1) * WINDOW_SECONDS - int(now))
        raise error
