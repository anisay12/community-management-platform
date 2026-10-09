from datetime import timedelta
from smtplib import SMTPException

import structlog
from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone, translation

from .models import DataExport, User
from .privacy import anonymize_user, render_export

logger = structlog.get_logger(__name__)


@shared_task(autoretry_for=(SMTPException, OSError), retry_backoff=True, max_retries=5)
def send_email(
    *, to: str, subject_template: str, body_template: str, context: dict, language: str
) -> None:
    """Render a plain-text email in ``language`` and send it to ``to``."""
    with translation.override(language):
        subject = render_to_string(subject_template, context)
        body = render_to_string(body_template, context)
    subject = " ".join(subject.split())
    EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [to]).send()


@shared_task
def build_data_export(export_id: int) -> None:
    """Write the JSON copy of a user's data and make it downloadable for a few days."""
    export = (
        DataExport.objects.select_related("user")
        .filter(pk=export_id, status=DataExport.Status.PENDING)
        .first()
    )
    if export is None:
        return
    try:
        content = render_export(export.user)
        export.file.save(f"{export.public_id}.json", ContentFile(content), save=False)
    except Exception:
        DataExport.objects.filter(pk=export.pk).update(status=DataExport.Status.FAILED)
        logger.exception("data_export_failed", export_id=export.pk)
        raise
    export.status = DataExport.Status.READY
    export.expires_at = timezone.now() + timedelta(days=settings.DATA_EXPORT_TTL_DAYS)
    export.save(update_fields=["status", "file", "expires_at"])


@shared_task
def purge_expired_exports() -> int:
    """Delete the expired exports and their files; return how many were deleted.

    Exports that never became ready (failed, or stuck pending after a lost worker) are
    deleted after the same delay, so they cannot block new requests forever.
    """
    now = timezone.now()
    stale = now - timedelta(days=settings.DATA_EXPORT_TTL_DAYS)
    expired = list(
        DataExport.objects.filter(
            Q(expires_at__lte=now) | Q(expires_at__isnull=True, created_at__lte=stale)
        )
    )
    storage = DataExport._meta.get_field("file").storage
    for export in expired:
        if export.file:
            storage.delete(export.file.name)
    DataExport.objects.filter(pk__in=[export.pk for export in expired]).delete()
    return len(expired)


@shared_task
def anonymize_expired_accounts() -> int:
    """Anonymize the accounts deactivated longer ago than the retention period."""
    cutoff = timezone.now() - timedelta(days=settings.ACCOUNT_ANONYMIZE_AFTER_DAYS)
    ids = list(
        User.objects.filter(
            status=User.Status.DEACTIVATED,
            deactivated_at__lte=cutoff,
            anonymized_at__isnull=True,
        ).values_list("pk", flat=True)
    )
    for pk in ids:
        anonymize_user(actor=None, user=User.objects.get(pk=pk))
    logger.info("accounts_anonymized", count=len(ids))
    return len(ids)
