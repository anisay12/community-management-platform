from datetime import date, datetime, time, timedelta

from django.db.models import Q, QuerySet
from django.utils import timezone

from .models import AuditEvent
from .policies import FUNCTIONAL, TECHNICAL, TECHNICAL_ACTION_PREFIXES, audit_scopes


def _technical_q() -> Q:
    query = Q()
    for prefix in TECHNICAL_ACTION_PREFIXES:
        query |= Q(action__startswith=prefix)
    return query


def events_visible_to(user) -> QuerySet[AuditEvent]:
    """Audit events within ``user``'s scope, newest first."""
    scopes = audit_scopes(user)
    if not scopes:
        return AuditEvent.objects.none()
    qs = AuditEvent.objects.select_related("actor").order_by("-created_at", "-pk")
    if scopes == {FUNCTIONAL, TECHNICAL}:
        return qs
    if scopes == {TECHNICAL}:
        return qs.filter(_technical_q())
    return qs.exclude(_technical_q())


def _start_of_day(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


def filter_events(
    qs: QuerySet[AuditEvent],
    *,
    action: str = "",
    actor: str = "",
    target: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
) -> QuerySet[AuditEvent]:
    """Narrow ``qs`` by the audit log filters; blank values are ignored.

    Dates are inclusive and read in the current time zone (``date_to`` covers its whole day).
    """
    if action:
        qs = qs.filter(action=action)
    if actor:
        qs = qs.filter(
            Q(actor__email__icontains=actor)
            | Q(actor__first_name__icontains=actor)
            | Q(actor__last_name__icontains=actor)
        )
    if target:
        qs = qs.filter(Q(target_id__icontains=target) | Q(target_type__iexact=target))
    if date_from:
        qs = qs.filter(created_at__gte=_start_of_day(date_from))
    if date_to:
        qs = qs.filter(created_at__lt=_start_of_day(date_to + timedelta(days=1)))
    return qs


def action_choices(qs: QuerySet[AuditEvent]) -> list[str]:
    """Sorted distinct actions present in ``qs`` (never reveals out-of-scope actions)."""
    return list(qs.order_by("action").values_list("action", flat=True).distinct())
