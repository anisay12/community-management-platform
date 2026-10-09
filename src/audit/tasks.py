from datetime import timedelta

import structlog
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import AuditEvent

logger = structlog.get_logger(__name__)


@shared_task
def purge_audit_events() -> int:
    cutoff = timezone.now() - timedelta(days=settings.AUDIT_RETENTION_DAYS)
    count = AuditEvent.purge_older_than(cutoff)
    logger.info("audit_events_purged", count=count, retention_days=settings.AUDIT_RETENTION_DAYS)
    return count
