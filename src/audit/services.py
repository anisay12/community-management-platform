import hashlib
import hmac
from typing import Any

from django.conf import settings

from core.context import get_request_context

from .models import AuditEvent

SENSITIVE_MARKERS = ("password", "token", "secret", "otp")


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean(item)
            for key, item in value.items()
            if not any(marker in str(key).lower() for marker in SENSITIVE_MARKERS)
        }
    if isinstance(value, list | tuple):
        return [_clean(item) for item in value]
    return value


def _hash_ip(ip: str | None) -> str:
    if not ip:
        return ""
    return hmac.new(settings.AUDIT_IP_HASH_KEY.encode(), ip.encode(), hashlib.sha256).hexdigest()


def record(
    *,
    actor,
    action: str,
    target,
    changes: dict | None = None,
    community_id: int | None = None,
) -> AuditEvent:
    """Append an audit event; call it inside the transaction of the change it describes."""
    if isinstance(target, tuple):
        target_type, target_id = target
    else:
        target_type = target._meta.label_lower
        target_id = getattr(target, "public_id", target.pk)
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None
    context = get_request_context()
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_type=str(target_type),
        target_id=str(target_id),
        community_id=community_id,
        changes=_clean(changes or {}),
        ip_hash=_hash_ip(context.ip),
        request_id=context.request_id or "",
    )
