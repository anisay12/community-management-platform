"""Idempotent API writes (spec § 2) over ``core.idempotency``, with the Idempotency-Key header.

The stored result is ``{status, kind, public_id, fingerprint}``. A replay with the same key
and the same request returns the original status (the view re-reads and serializes the
object's current state); the same key with a different request answers 409. Scopes are the
UI views' scope strings, so one key can never run a write twice through either door.
"""

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from django.utils.translation import gettext as _
from redis.exceptions import RedisError

from core import idempotency
from core.errors import DomainError

HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"


@dataclass(frozen=True)
class Outcome:
    status: int
    kind: str
    public_id: str
    replayed: bool = False


def _reused() -> DomainError:
    return DomainError(
        "idempotency_key_reused",
        _("This Idempotency-Key was already used for a different request."),
    )


def _key(request, required: bool) -> str | None:
    raw = request.headers.get(HEADER, "").strip()
    if not raw:
        if required:
            raise DomainError(
                "idempotency_key_required",
                _("This request needs an Idempotency-Key header (a UUID)."),
            )
        return None
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        raise DomainError(
            "idempotency_key_required", _("The Idempotency-Key header must be a UUID.")
        ) from None


def fingerprint(scope: str, data, files) -> str:
    """SHA-256 of the scope, the non-file fields and each file's name and size."""
    if hasattr(data, "getlist"):
        fields = {name: data.getlist(name) for name in sorted(data) if name not in files}
    else:
        fields = {name: data[name] for name in sorted(data) if name not in files}
    file_parts = sorted((name, upload.name, upload.size) for name, upload in files.items())
    payload = json.dumps(
        {"scope": scope, "fields": fields, "files": file_parts}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def run_idempotent(
    request, scope: str, operation: Callable[[], Outcome], *, required: bool = False
) -> Outcome:
    key = _key(request, required)
    if key is None:
        return operation()
    print_ = fingerprint(scope, request.data, request.FILES)

    started = False

    def stored() -> dict:
        nonlocal started
        started = True
        outcome = operation()
        return {
            "status": outcome.status,
            "kind": outcome.kind,
            "public_id": outcome.public_id,
            "fingerprint": print_,
        }

    try:
        result, replayed = idempotency.run_once(request.user, scope, key, stored)
    except DomainError:
        raise
    except (ConnectionError, OSError, RedisError) as error:
        if started:
            raise  # the write itself failed: a server error, not a cache outage
        # Fail closed: without the cache we cannot guarantee "at most once" (spec § 16).
        raise DomainError(
            "idempotency_unavailable",
            _("This action is temporarily unavailable. Please try again in a moment."),
        ) from error
    if not isinstance(result, dict) or result.get("fingerprint") != print_:
        raise _reused()
    return Outcome(result["status"], result["kind"], result["public_id"], replayed)


def mark_replayed(response, outcome: Outcome):
    if outcome.replayed:
        response[REPLAYED_HEADER] = "true"
    return response
