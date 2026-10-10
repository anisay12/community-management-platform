"""Idempotent form submissions (spec § 2, "Idempotence of sensitive forms").

A sensitive form carries a hidden ``idempotency_key`` (a UUID generated when the form is
rendered). The view runs its write through ``run_once``: the first submission runs it and
remembers its result for ``RESULT_TTL_SECONDS`` (24 h) in the shared cache (Redis in
production), under a key made of the user, the form and the submitted key. A second submission
of the same form (double click, browser resend, "back" then submit again) gets the remembered
result back without running the write again.

Concurrency: the first request claims the key with ``cache.add`` (atomic in Redis) before it
runs; a request arriving while the first one is still running finds the claim and is refused
with ``DomainError("submission_in_progress")`` instead of running the write a second time. The
claim expires after ``IN_FLIGHT_TTL_SECONDS`` so a crashed worker never blocks the key for 24 h.
When the write raises, the claim is released: the user may correct the form and submit again
with the same key.

Only results that can be pickled are stored; views keep them small (a ``public_id`` string).
"""

import uuid
from collections.abc import Callable
from typing import Any

from django.core.cache import cache
from django.utils.translation import gettext as _

from core.errors import DomainError

RESULT_TTL_SECONDS = 24 * 60 * 60
# Long enough for a slow upload; short enough that a crash never blocks a form for long.
IN_FLIGHT_TTL_SECONDS = 15 * 60
CACHE_PREFIX = "idempotency"

_IN_FLIGHT = "__in_flight__"


def new_key() -> str:
    """A fresh key for a form being rendered."""
    return str(uuid.uuid4())


def cache_key(user, scope: str, key) -> str:
    """The cache key of submission ``key`` of form ``scope`` by ``user``."""
    return f"{CACHE_PREFIX}:{user.pk}:{scope}:{key}"


def _stored(value) -> tuple[bool, Any]:
    if isinstance(value, dict) and "result" in value:
        return True, value["result"]
    return False, None


def run_once(user, scope: str, key, operation: Callable[[], Any]) -> tuple[Any, bool]:
    """Run ``operation`` once per ``(user, scope, key)``: ``(result, replayed)``.

    ``replayed`` is true when the result comes from an earlier submission (``operation`` was
    not called). Raises ``DomainError("submission_in_progress")`` while the same submission
    is still running in another request; any exception raised by ``operation`` releases the
    key and propagates.
    """
    name = cache_key(user, scope, key)
    found, result = _stored(cache.get(name))
    if found:
        return result, True
    if not cache.add(name, _IN_FLIGHT, timeout=IN_FLIGHT_TTL_SECONDS):
        # Someone holds the key: finished in between (result stored) or still running.
        found, result = _stored(cache.get(name))
        if found:
            return result, True
        raise DomainError(
            "submission_in_progress",
            _("This form is already being processed. Wait a moment, then check the result."),
        )
    try:
        result = operation()
    except BaseException:
        cache.delete(name)
        raise
    cache.set(name, {"result": result}, timeout=RESULT_TTL_SECONDS)
    return result, False
