from datetime import timedelta
from smtplib import SMTPException

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from celery.utils.time import get_exponential_backoff_interval
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone, translation

from audit.services import record
from core.errors import DomainError
from documents import scanner
from documents import storage as file_storage

from . import avatars
from .models import DataExport, User, UserProfile
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
            # Same lock order as anonymize_user (account, then its exports), so the two
            # run one after the other. The account may have been anonymized, and the
            # export deleted, while the file was being built: then the file goes.
            account_open = (
                User.objects.select_for_update()
                .filter(pk=export.user_id, anonymized_at__isnull=True)
                .values_list("pk", flat=True)
                .first()
                is not None
            )
            locked = (
                DataExport.objects.select_for_update()
                .filter(pk=export.pk, status=DataExport.Status.PENDING)
                .first()
            )
            if not account_open or locked is None:
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


# --- profile photo: antivirus scan and re-encoding ---------------------------------------

AVATAR_SCAN_MAX_RETRIES = 5
AVATAR_SCAN_RETRY_BACKOFF_SECONDS = 30
AVATAR_SCAN_RETRY_BACKOFF_MAX_SECONDS = 900


def _locked_pending_profile(profile_pk: int, key: str):
    """The profile under a row lock while ``key`` is still its pending upload, else ``None``
    (replaced, removed or already concluded: idempotence)."""
    return (
        UserProfile.objects.select_for_update()
        .filter(
            pk=profile_pk,
            avatar_pending_key=key,
            avatar_scan_status=UserProfile.AvatarScan.PENDING,
        )
        .first()
    )


def _avatar_fail(profile_pk: int, key: str, status: str, *, infected_signature: str = "") -> bool:
    """Conclude a pending upload as ``infected`` or ``error``: the quarantined object is
    deleted with the verdict; returns whether this run concluded it."""
    with transaction.atomic():
        profile = _locked_pending_profile(profile_pk, key)
        if profile is None:
            return False
        profile.avatar_pending_key = ""
        profile.avatar_scan_status = status
        profile.save(update_fields=["avatar_pending_key", "avatar_scan_status"])
        if status == UserProfile.AvatarScan.INFECTED:
            record(
                actor=None,
                action="profile.avatar_infected",
                target=profile.user,
                changes={"signature": infected_signature[:200]},
            )
        # Inside the transaction: if the delete fails the upload stays pending and the
        # verdict is retried; an infected object never outlives a committed verdict.
        file_storage.delete(key)
    return True


def _avatar_conclude_clean(profile_pk: int, key: str) -> None:
    try:
        with file_storage.open_stream(key) as stream:
            content = avatars.reencode(stream)
    except DomainError as exc:
        _avatar_fail(profile_pk, key, UserProfile.AvatarScan.ERROR)
        logger.warning("accounts.avatar.unreadable", profile=profile_pk, code=exc.code)
        return
    new_key = avatars.save_clean(content)
    with transaction.atomic():
        profile = _locked_pending_profile(profile_pk, key)
        if profile is None:
            file_storage.discard(new_key)
            return
        previous = profile.avatar.name if profile.avatar else ""
        profile.avatar.name = new_key
        profile.avatar_pending_key = ""
        profile.avatar_scan_status = UserProfile.AvatarScan.NONE
        profile.save(update_fields=["avatar", "avatar_pending_key", "avatar_scan_status"])
        transaction.on_commit(lambda: [file_storage.discard(name) for name in (key, previous)])
    logger.info("accounts.avatar.clean", profile=profile_pk)


@shared_task(bind=True, max_retries=AVATAR_SCAN_MAX_RETRIES)
def scan_avatar(self, profile_pk: int, key: str) -> None:
    """Scan the quarantined upload ``key`` of one profile, then publish or refuse it.

    - Clean: decoded and re-encoded (``accounts.avatars.reencode``) under a new random key
      that becomes ``UserProfile.avatar``; the previous photo and the original are deleted.
      An image that cannot be decoded concludes ``error``.
    - Infected: the object is deleted, the status becomes ``infected``; audited
      ``profile.avatar_infected`` (no actor).
    - ``ScannerUnavailable``: retried with exponential backoff, ``error`` after the last try
      (same approach as ``documents.tasks.scan_document_version``).

    Does nothing once ``key`` is no longer the profile's pending upload.
    """
    if not UserProfile.objects.filter(
        pk=profile_pk, avatar_pending_key=key, avatar_scan_status=UserProfile.AvatarScan.PENDING
    ).exists():
        return
    try:
        with file_storage.open_stream(key) as stream:
            result = scanner.scan_stream(stream)
    except scanner.ScannerUnavailable as exc:
        if self.request.retries >= AVATAR_SCAN_MAX_RETRIES:
            _avatar_fail(profile_pk, key, UserProfile.AvatarScan.ERROR)
            logger.error("accounts.avatar.scan_failed", profile=profile_pk, detail=str(exc))
            return
        countdown = get_exponential_backoff_interval(
            factor=AVATAR_SCAN_RETRY_BACKOFF_SECONDS,
            retries=self.request.retries,
            maximum=AVATAR_SCAN_RETRY_BACKOFF_MAX_SECONDS,
            full_jitter=True,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc
    except FileNotFoundError:
        _avatar_fail(profile_pk, key, UserProfile.AvatarScan.ERROR)
        return
    if result.clean:
        _avatar_conclude_clean(profile_pk, key)
    else:
        _avatar_fail(
            profile_pk, key, UserProfile.AvatarScan.INFECTED, infected_signature=result.signature
        )
        logger.warning("accounts.avatar.infected", profile=profile_pk)
