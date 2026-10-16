"""Idempotent API writes (spec § 2) over ``core.idempotency``, with the Idempotency-Key header.

The stored result is ``{status, kind, public_id, fingerprint}``. A replay with the same key
and the same request returns the original status (the view re-reads and serializes the
object's current state); the same key with a different request answers 409. Scopes are the
UI views' scope strings, so one key can never run a write twice through either door.

Known limit: if the write succeeded but storing its result in the cache then fails, the client
gets a 500 although the write happened; a retry with the same key then answers 409
``submission_in_progress`` until the in-flight claim expires.
"""

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
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
    """SHA-256 of the scope, the body and each uploaded file's field, name and size.

    A mapping body is hashed field by field (form fields keep every value); any other JSON
    body (list, number, string) is hashed whole.
    """
    if isinstance(data, Mapping):
        if hasattr(data, "getlist"):
            body = {name: data.getlist(name) for name in sorted(data) if name not in files}
        else:
            body = {name: data[name] for name in sorted(data) if name not in files}
    else:
        body = data
    file_parts = sorted(
        (field, upload.name, upload.size) for field, uploads in files.lists() for upload in uploads
    )
    payload = json.dumps(
        {"scope": scope, "fields": body, "files": file_parts}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def run_idempotent(
    request, scope: str, operation: Callable[[], Outcome], *, required: bool = False
) -> Outcome:
    key = _key(request, required)
    if key is None:
        return operation()
    request_fingerprint = fingerprint(scope, request.data, request.FILES)

    started = False

    def stored() -> dict:
        nonlocal started
        started = True
        outcome = operation()
        return {
            "status": outcome.status,
            "kind": outcome.kind,
            "public_id": outcome.public_id,
            "fingerprint": request_fingerprint,
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
    if not isinstance(result, dict) or result.get("fingerprint") != request_fingerprint:
        raise _reused()
    return Outcome(result["status"], result["kind"], result["public_id"], replayed)


def mark_replayed(response, outcome: Outcome):
    if outcome.replayed:
        response[REPLAYED_HEADER] = "true"
    return response
