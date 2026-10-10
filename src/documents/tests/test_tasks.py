from datetime import timedelta

import pytest
from django.core.files.storage import default_storage
from django.utils import timezone

from audit.models import AuditEvent
from communities.models import CommunityMembership
from config.celery import app as celery_app
from documents import policies, scanner, services, storage, tasks
from documents.models import Document, DocumentVersion, DownloadLog
from documents.scanner import ScannerUnavailable, ScanResult
from notifications.models import Notification

from .samples import EICAR, upload

Role = CommunityMembership.Role
Level = CommunityMembership.NotificationLevel
Status = DocumentVersion.ScanStatus


class FakeScanner:
    """Stands in for ``scanner.scan_stream``: EICAR is infected, anything else clean, unless
    ``failures`` asks for ``ScannerUnavailable`` first."""

    def __init__(self, failures=0):
        self.failures = failures
        self.calls = 0

    def __call__(self, stream):
        self.calls += 1
        if self.calls <= self.failures:
            raise ScannerUnavailable("clamd unreachable: ConnectionRefusedError")
        if EICAR in stream.read():
            return ScanResult(clean=False, signature="Eicar-Test-Signature")
        return ScanResult(clean=True)


@pytest.fixture
def fake_scanner(monkeypatch):
    def _install(failures=0):
        fake = FakeScanner(failures)
        monkeypatch.setattr(scanner, "scan_stream", fake)
        return fake

    return _install


@pytest.fixture
def eager_retries(monkeypatch):
    """Eager tasks that propagate exceptions surface ``Retry`` instead of running the retry:
    let Celery's eager ``apply`` loop run the retries, as a worker would."""
    monkeypatch.setitem(celery_app.conf, "CELERY_TASK_EAGER_PROPAGATES", False)


@pytest.fixture
def community(make_community):
    return make_community()


@pytest.fixture
def owner(make_user, community, add_member):
    user = make_user("owner@example.com", first_name="Olivia")
    add_member(community, user, Role.CONTRIBUTOR)
    return user


@pytest.fixture
def moderator(make_user, community, add_member):
    user = make_user("moderator@example.com", first_name="Mo")
    add_member(community, user, Role.MODERATOR)
    return user


def _member(make_user, add_member, community, email, role=Role.MEMBER, level=Level.HIGHLIGHTS):
    user = make_user(email)
    membership = add_member(community, user, role)
    CommunityMembership.objects.filter(pk=membership.pk).update(notification_level=level)
    return user


def _scan(version, capture):
    with capture(execute=True):
        tasks.scan_document_version.delay(version.pk)
    version.refresh_from_db()
    return version


# --- clean ------------------------------------------------------------------------------


def test_clean_first_version_is_promoted_and_published(
    community,
    owner,
    moderator,
    make_user,
    add_member,
    make_document,
    fake_scanner,
    django_capture_on_commit_callbacks,
):
    fake_scanner()
    everything = _member(make_user, add_member, community, "all@example.com", level=Level.ALL)
    highlights = _member(make_user, add_member, community, "hl@example.com")
    _member(make_user, add_member, community, "quiet@example.com", level=Level.NONE)
    document = make_document(community, owner, scan_status=Status.PENDING)
    version = document.current_version
    quarantine_key = version.storage_key
    assert not policies.can_download(highlights, document)

    version = _scan(version, django_capture_on_commit_callbacks)

    assert version.scan_status == Status.CLEAN
    assert version.scanned_at is not None
    assert version.storage_key == "documents/" + quarantine_key.removeprefix("quarantine/")
    assert default_storage.exists(version.storage_key)
    assert not default_storage.exists(quarantine_key)
    document.refresh_from_db()
    assert document.current_version == version and version.is_reference
    assert policies.can_download(highlights, document)
    notified = set(
        Notification.objects.filter(category="document_published").values_list(
            "recipient__email", flat=True
        )
    )
    # Levels all and highlights (the moderator's default is highlights); not the uploader.
    assert notified == {everything.email, highlights.email, moderator.email}
    notification = Notification.objects.filter(category="document_published").first()
    assert notification.target_id == str(document.public_id)
    assert notification.actor == owner


def test_restricted_document_broadcast_to_readers_only(
    community,
    owner,
    make_user,
    add_member,
    make_document,
    fake_scanner,
    django_capture_on_commit_callbacks,
):
    fake_scanner()
    _member(make_user, add_member, community, "member@example.com", level=Level.ALL)
    expert = _member(make_user, add_member, community, "expert@example.com", Role.EXPERT, Level.ALL)
    document = make_document(
        community,
        owner,
        scan_status=Status.PENDING,
        visibility=Document.Visibility.RESTRICTED,
        min_role=Role.EXPERT,
    )
    _scan(document.current_version, django_capture_on_commit_callbacks)
    recipients = list(
        Notification.objects.filter(category="document_published").values_list(
            "recipient", flat=True
        )
    )
    assert recipients == [expert.pk]


def test_broadcast_in_batches(community, owner, make_user, add_member, make_document, monkeypatch):
    monkeypatch.setattr(tasks, "BROADCAST_BATCH_SIZE", 2)
    for index in range(5):
        _member(make_user, add_member, community, f"m{index}@example.com", level=Level.ALL)
    document = make_document(community, owner)
    calls = []
    original = tasks.notify
    monkeypatch.setattr(
        tasks,
        "notify",
        lambda *args, **kwargs: (calls.append(len(args[1])), original(*args, **kwargs)),
    )
    tasks.broadcast_document(document.pk)
    assert calls == [2, 2, 1]


def test_new_version_becomes_reference_only_once_clean(
    community, owner, make_document, fake_scanner, django_capture_on_commit_callbacks
):
    fake_scanner()
    document = make_document(community, owner)
    first = document.current_version
    version = services.add_version(actor=owner, document=document, upload=upload())
    document.refresh_from_db()
    assert document.current_version == first
    version = _scan(version, django_capture_on_commit_callbacks)
    document.refresh_from_db()
    first.refresh_from_db()
    assert document.current_version == version
    assert version.is_reference and not version.promote_on_clean
    assert not first.is_reference
    assert not Notification.objects.filter(category="document_published").exists()


def test_version_without_make_reference_keeps_reference(
    community, owner, make_document, fake_scanner, django_capture_on_commit_callbacks
):
    fake_scanner()
    document = make_document(community, owner)
    first = document.current_version
    version = services.add_version(
        actor=owner, document=document, upload=upload(), make_reference=False
    )
    version = _scan(version, django_capture_on_commit_callbacks)
    document.refresh_from_db()
    assert version.scan_status == Status.CLEAN and not version.is_reference
    assert document.current_version == first


def test_clean_version_replaces_infected_reference(
    community, owner, make_document, fake_scanner, django_capture_on_commit_callbacks
):
    fake_scanner()
    document = make_document(community, owner, scan_status=Status.INFECTED, content=EICAR)
    version = services.add_version(
        actor=owner, document=document, upload=upload(), make_reference=False
    )
    version = _scan(version, django_capture_on_commit_callbacks)
    document.refresh_from_db()
    assert document.current_version == version


# --- infected ---------------------------------------------------------------------------


def test_infected_version_is_deleted_and_reported(
    community,
    owner,
    moderator,
    make_user,
    add_member,
    make_document,
    fake_scanner,
    django_capture_on_commit_callbacks,
):
    fake_scanner()
    reader = _member(make_user, add_member, community, "reader@example.com", level=Level.ALL)
    document = make_document(community, owner, scan_status=Status.PENDING, content=EICAR)
    version = document.current_version
    key = version.storage_key

    version = _scan(version, django_capture_on_commit_callbacks)

    assert version.scan_status == Status.INFECTED
    assert version.scan_detail == "Eicar-Test-Signature"
    assert version.storage_key == ""
    assert not default_storage.exists(key)
    document.refresh_from_db()
    for user in (owner, moderator, reader):
        assert not policies.can_download(user, document)
    assert not policies.can_view_document(reader, document)
    event = AuditEvent.objects.get(action="document.scan_infected")
    assert event.actor is None
    assert event.changes == {"version": "1", "signature": "Eicar-Test-Signature"}
    alerted = set(
        Notification.objects.filter(category="document_infected").values_list(
            "recipient", flat=True
        )
    )
    assert alerted == {owner.pk, moderator.pk}
    assert not Notification.objects.filter(category="document_published").exists()


def test_infected_new_version_leaves_reference(
    community, owner, make_document, fake_scanner, django_capture_on_commit_callbacks
):
    fake_scanner()
    document = make_document(community, owner)
    first = document.current_version
    version = services.add_version(
        actor=owner, document=document, upload=upload("v2.txt", b"x " + EICAR)
    )
    version = _scan(version, django_capture_on_commit_callbacks)
    document.refresh_from_db()
    assert version.scan_status == Status.INFECTED and not version.promote_on_clean
    assert document.current_version == first
    assert not policies.can_download(owner, document, version)


# --- errors and idempotence -------------------------------------------------------------


def test_scanner_unavailable_retries_then_error(
    eager_retries, community, owner, make_document, fake_scanner, django_capture_on_commit_callbacks
):
    fake = fake_scanner(failures=99)
    document = make_document(community, owner, scan_status=Status.PENDING)
    version = _scan(document.current_version, django_capture_on_commit_callbacks)
    assert fake.calls == tasks.SCAN_MAX_RETRIES + 1
    assert version.scan_status == Status.ERROR
    assert "unreachable" in version.scan_detail
    assert version.storage_key.startswith("quarantine/")  # kept for a later rescan
    assert not policies.can_download(owner, document)


def test_scanner_recovers_within_retries(
    eager_retries, community, owner, make_document, fake_scanner, django_capture_on_commit_callbacks
):
    fake = fake_scanner(failures=2)
    document = make_document(community, owner, scan_status=Status.PENDING)
    version = _scan(document.current_version, django_capture_on_commit_callbacks)
    assert fake.calls == 3
    assert version.scan_status == Status.CLEAN


def test_retry_uses_exponential_backoff(community, owner, make_document, fake_scanner, monkeypatch):
    fake_scanner(failures=99)
    document = make_document(community, owner, scan_status=Status.PENDING)
    countdowns = []

    class Stop(Exception):
        pass

    def fake_retry(*, exc, countdown):
        countdowns.append(countdown)
        raise Stop

    monkeypatch.setattr(tasks.scan_document_version, "retry", fake_retry)
    with pytest.raises(Stop):
        tasks.scan_document_version.apply(args=(document.current_version.pk,), throw=True)
    assert len(countdowns) == 1
    assert 0 <= countdowns[0] <= tasks.SCAN_RETRY_BACKOFF_SECONDS


@pytest.mark.parametrize("status", [Status.CLEAN, Status.INFECTED, Status.ERROR])
def test_concluded_version_left_alone(
    community, owner, make_document, fake_scanner, status, django_capture_on_commit_callbacks
):
    fake = fake_scanner()
    document = make_document(community, owner, scan_status=status)
    before = DocumentVersion.objects.values().get(pk=document.current_version.pk)
    _scan(document.current_version, django_capture_on_commit_callbacks)
    assert fake.calls == 0
    assert DocumentVersion.objects.values().get(pk=document.current_version.pk) == before


def test_missing_object_marks_error(
    community, owner, make_document, fake_scanner, django_capture_on_commit_callbacks
):
    fake_scanner()
    document = make_document(community, owner, scan_status=Status.PENDING)
    default_storage.delete(document.current_version.storage_key)
    version = _scan(document.current_version, django_capture_on_commit_callbacks)
    assert version.scan_status == Status.ERROR


def test_promote_is_repeatable():
    key = storage.save_to_quarantine(upload())
    target = storage.promote(key)
    assert target.startswith("documents/")
    assert storage.promote(key) == target  # source gone, copy present: done
    assert storage.promote(target) == target
    with storage.open_stream(target) as stream:
        assert stream.read().startswith(b"%PDF")
    storage.delete(target)
    assert not default_storage.exists(target)


# --- lifecycle --------------------------------------------------------------------------


def test_expire_documents(community, owner, make_document):
    past = timezone.now() - timedelta(hours=1)
    due = make_document(community, owner, expires_at=past)
    later = make_document(community, owner, expires_at=timezone.now() + timedelta(days=1))
    archived = make_document(community, owner, expires_at=past, status=Document.Status.ARCHIVED)
    forever = make_document(community, owner)
    assert tasks.expire_documents() == 1
    statuses = dict(Document.objects.values_list("pk", "status"))
    assert statuses == {
        due.pk: Document.Status.EXPIRED,
        later.pk: Document.Status.ACTIVE,
        archived.pk: Document.Status.ARCHIVED,
        forever.pk: Document.Status.ACTIVE,
    }
    event = AuditEvent.objects.get(action="document.expire")
    assert event.actor is None and event.target_id == str(due.public_id)
    assert tasks.expire_documents() == 0


def _log(document, user, *, days_ago=0, is_preview=False):
    return DownloadLog.objects.create(
        document=document,
        version=document.current_version,
        user=user,
        is_preview=is_preview,
        created_at=timezone.now() - timedelta(days=days_ago),
    )


def test_purge_download_logs(community, owner, make_document, settings, monkeypatch):
    settings.DOWNLOAD_LOG_RETENTION_DAYS = 30
    monkeypatch.setattr(tasks, "PURGE_BATCH_SIZE", 2)
    document = make_document(community, owner)
    old = [_log(document, owner, days_ago=31) for _ in range(3)]
    recent = _log(document, owner, days_ago=29)
    assert tasks.purge_download_logs() == len(old)
    assert list(DownloadLog.objects.all()) == [recent]


def test_verify_download_counters(community, owner, make_document):
    drifted = make_document(community, owner, download_count=7)
    correct = make_document(community, owner)
    _log(drifted, owner)
    _log(drifted, owner, is_preview=True)
    _log(correct, owner)
    Document.objects.filter(pk=correct.pk).update(download_count=1)
    assert tasks.verify_download_counters() == 1
    counts = dict(Document.objects.values_list("pk", "download_count"))
    assert counts == {drifted.pk: 1, correct.pk: 1}


def test_beat_schedule_has_document_jobs(settings):
    scheduled = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    assert {
        "documents.tasks.expire_documents",
        "documents.tasks.purge_download_logs",
        "documents.tasks.verify_download_counters",
    } <= scheduled
