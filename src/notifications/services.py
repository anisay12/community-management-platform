from collections.abc import Iterable

from django.db import transaction

from .models import Notification


def notify(category: str, recipients: Iterable, *, actor=None, target=None, community=None) -> None:
    """Store one in-app notification per distinct active recipient once the transaction commits.

    The actor is never notified of their own action. ``target`` is a model instance (its
    ``public_id`` or pk identifies it) or a ``(type, id)`` tuple, as in ``audit.services.record``.
    """
    if target is None:
        target_type, target_id = "", ""
    elif isinstance(target, tuple):
        target_type, target_id = target
    else:
        target_type = target._meta.label_lower
        target_id = getattr(target, "public_id", target.pk)
    actor_pk = getattr(actor, "pk", None) if getattr(actor, "is_authenticated", False) else None
    seen: set = set()
    rows = []
    for user in recipients:
        if user.pk in seen or user.pk == actor_pk or not user.is_active:
            continue
        seen.add(user.pk)
        rows.append(
            Notification(
                recipient=user,
                category=category,
                actor_id=actor_pk,
                target_type=str(target_type),
                target_id=str(target_id),
                community=community,
            )
        )
    if rows:
        transaction.on_commit(lambda: Notification.objects.bulk_create(rows))
