"""Coarse per-user anti-abuse limits for the API.

The precise per-action limits (posts, comments, reactions per hour) stay in the services
(``posts.ratelimit``) and are shared with the UI; these sit well above them.
"""

import structlog
from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import UserRateThrottle

logger = structlog.get_logger(__name__)


class _FailOpenThrottle(UserRateThrottle):
    """When the cache (Redis) is down, allow the request and log it, like posts.ratelimit
    (spec § 16: Redis unavailable → degraded, not down)."""

    applies_to_safe_methods: bool

    def allow_request(self, request, view) -> bool:
        if (request.method in SAFE_METHODS) != self.applies_to_safe_methods:
            return True
        try:
            return super().allow_request(request, view)
        except Exception:  # cache backend down: never block the API on it
            logger.warning("api.throttle.unavailable", scope=self.scope, exc_info=True)
            return True


class ReadThrottle(_FailOpenThrottle):
    scope = "api_read"
    applies_to_safe_methods = True


class WriteThrottle(_FailOpenThrottle):
    scope = "api_write"
    applies_to_safe_methods = False
