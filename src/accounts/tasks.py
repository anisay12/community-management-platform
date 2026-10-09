from datetime import timedelta
from smtplib import SMTPException

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone, translation

from .models import DataExport, User
from .privacy import anonymize_user, render_export

logger = structlog.get_logger(__name__)

# Errors a retry may cure: the file storage is a local disk or an S3 bucket.
TRANSIENT_STORAGE_ERRORS = (OSError, BotoCoreError, ClientError)


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


def _discard_stored_file(storage, name: str) -> None:
    """Delete a file written for an export that will not use it; never raise."""
    try:
        storage.delete(name)
    except Exception:
        logger.exception("data_export_file_cleanup_failed")


@shared_task(bind=True, autoretry_for=TRANSIENT_STORAGE_ERRORS, retry_backoff=True, max_retries=3)
def build_data_export(self, export_id: int) -> None:
    """Write the JSON copy of a user's data and make it downloadable for a few days.

    A transient storage error leaves the export pending so that Celery retries it; it
    is marked failed on any other error, or once the retries are used up.
    """
    export = (
        DataExport.objects.select_related("user")
        .filter(pk=export_id, status=DataExport.Status.PENDING)
        .first()
    )
    if export is None:
        return
    field = DataExport._meta.get_field("file")
    storage = field.storage
    name = ""
    try:
        content = render_export(export.user)
        name = storage.save(
            field.generate_filename(export, f"{export.public_id}.json"), ContentFile(content)
        )
        with transaction.atomic():
            # The export may have been deleted (account anonymized) while it was built.
            locked = (
                DataExport.objects.select_for_update()
                .filter(pk=export.pk, status=DataExport.Status.PENDING)
                .first()
            )
            if locked is None:
                _discard_stored_file(storage, name)
                return
            locked.file.name = name
            locked.status = DataExport.Status.READY
            locked.expires_at = timezone.now() + timedelta(days=settings.DATA_EXPORT_TTL_DAYS)
            locked.save(update_fields=["status", "file", "expires_at"])
    except Exception as error:
        if name:
            _discard_stored_file(storage, name)
        will_retry = isinstance(error, TRANSIENT_STORAGE_ERRORS) and (
            self.request.retries < self.max_retries
        )
        if not will_retry:
            DataExport.objects.filter(pk=export.pk).update(status=DataExport.Status.FAILED)
        logger.exception("data_export_failed", export_id=export.pk, will_retry=will_retry)
        raise


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
    accounts = list(
        User.objects.filter(
            status=User.Status.DEACTIVATED,
            deactivated_at__lte=cutoff,
            anonymized_at__isnull=True,
        ).values_list("pk", "public_id")
    )
    done = 0
    for pk, public_id in accounts:
        # One transaction per account: a failure leaves that account untouched and the
        # others are still processed. Only the public identifier is logged.
        try:
            with transaction.atomic():
                anonymize_user(actor=None, user=User.objects.get(pk=pk))
        except Exception as error:
            logger.error(
                "account_anonymization_failed",
                user_public_id=str(public_id),
                error_type=type(error).__name__,
            )
        else:
            done += 1
    logger.info("accounts_anonymized", count=done, failed=len(accounts) - done)
    return done
