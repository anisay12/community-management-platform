import json
import threading
from datetime import timedelta

import pytest
import structlog
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.sessions.backends.cache import SessionStore
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone, translation
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts import privacy, services, tasks
from accounts.models import DataExport, ExternalIdentity, User, UserSession
from accounts.roles import Role
from audit.models import AuditEvent
from conftest import PASSWORD
from core.errors import DomainError
from organizations.models import Employment, OrganizationUnit
from taxonomy.models import Tag

pytestmark = pytest.mark.django_db


@pytest.fixture
def unit(db):
    return OrganizationUnit.objects.create(name="Data", code="DATA")


@pytest.fixture
def manager(make_user):
    return make_user("manager@example.com", first_name="Mia", last_name="Boss")


@pytest.fixture
def owner(make_user, unit, manager):
    user = make_user("owner@example.com", first_name="Olga", last_name="Owner")
    user.groups.add(Group.objects.get(name=Role.EMPLOYEE))
    profile = user.profile
    profile.job_title = "Data engineer"
    profile.bio = "Loves pipelines."
    profile.language = "fr"
    profile.profile_visibility = profile.Visibility.PRIVATE
    profile.save()
    profile.interests.add(Tag.objects.create(name="Python", slug="python"))
    Employment.objects.create(user=user, unit=unit, manager=manager)
    ExternalIdentity.objects.create(user=user, provider="entra", subject="secret-subject-42")
    return user


@pytest.fixture
def other(make_user):
    return make_user("other@example.com", first_name="Otto", last_name="Stranger")


@pytest.fixture
def admin_client(client, functional_admin, verified_login):
    return verified_login(client, functional_admin)


def _ready_export(owner, capture) -> DataExport:
    with capture(execute=True):
        export = services.request_data_export(user=owner)
    export.refresh_from_db()
    assert export.status == DataExport.Status.READY
    return export


def _raw(export) -> str:
    with export.file.open("rb") as handle:
        return handle.read().decode("utf-8")


def _content(export) -> dict:
    return json.loads(_raw(export))


# Export ---------------------------------------------------------------------------


def test_request_data_export_builds_a_ready_json_file(owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        export = services.request_data_export(user=owner)
    export.refresh_from_db()
    assert export.status == DataExport.Status.READY
    expected = timezone.now() + timedelta(days=settings.DATA_EXPORT_TTL_DAYS)
    assert abs(export.expires_at - expected) < timedelta(minutes=1)
    assert export.file.name.startswith("exports/")
    data = _content(export)
    assert set(data) == {"generated_at", "sections"}
    assert {"account", "audit"} <= set(data["sections"])
    assert AuditEvent.objects.filter(
        action="user.data_export_requested", actor=owner, target_id=str(owner.public_id)
    ).exists()


def test_export_contains_own_data_and_nothing_about_other_users(
    owner, other, django_capture_on_commit_callbacks
):
    services.set_roles(actor=other, user=other, roles=[Role.EMPLOYEE])
    with django_capture_on_commit_callbacks(execute=True):
        export = services.request_data_export(user=owner)
    export.refresh_from_db()
    raw = _raw(export)
    data = json.loads(raw)
    account = data["sections"]["account"]
    assert account["email"] == "owner@example.com"
    assert account["first_name"] == "Olga"
    assert account["status"] == "active"
    assert account["profile"]["job_title"] == "Data engineer"
    assert account["profile"]["interests"] == ["Python"]
    assert account["employment"] == {"unit": "DATA", "manager_email": "manager@example.com"}
    assert account["roles"] == [Role.EMPLOYEE.value]
    assert [i["provider"] for i in account["external_identities"]] == ["entra"]
    assert "secret-subject-42" not in raw
    actions = {event["action"] for event in data["sections"]["audit"]}
    assert "user.data_export_requested" in actions
    assert "user.roles_changed" not in actions  # done by another actor
    assert "other@example.com" not in raw
    assert "Stranger" not in raw


def test_export_file_is_utf8_with_indent(owner, django_capture_on_commit_callbacks):
    owner.first_name = "Élodie"
    owner.save()
    with django_capture_on_commit_callbacks(execute=True):
        export = services.request_data_export(user=owner)
    export.refresh_from_db()
    raw = _raw(export)
    assert "Élodie" in raw
    assert '\n  "sections"' in raw


def test_only_one_export_at_a_time(owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        first = services.request_data_export(user=owner)
    with django_capture_on_commit_callbacks(execute=True):
        second = services.request_data_export(user=owner)
    assert first.pk == second.pk
    assert DataExport.objects.filter(user=owner).count() == 1
    assert AuditEvent.objects.filter(action="user.data_export_requested").count() == 1


def test_new_export_allowed_after_expiry_or_failure(owner, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        first = services.request_data_export(user=owner)
    DataExport.objects.filter(pk=first.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    with django_capture_on_commit_callbacks(execute=True):
        second = services.request_data_export(user=owner)
    assert second.pk != first.pk
    DataExport.objects.filter(pk=second.pk).update(status=DataExport.Status.FAILED)
    third = services.request_data_export(user=owner)
    assert third.pk not in {first.pk, second.pk}


def test_build_failure_marks_export_failed(owner, monkeypatch):
    export = DataExport.objects.create(user=owner)

    def broken(user):
        raise RuntimeError("boom")

    monkeypatch.setitem(privacy._exporters, "broken", broken)
    with pytest.raises(RuntimeError):
        tasks.build_data_export(export.pk)
    export.refresh_from_db()
    assert export.status == DataExport.Status.FAILED
    assert not export.file


def test_build_removes_the_file_when_the_export_was_deleted_meanwhile(owner, monkeypatch):
    export = DataExport.objects.create(user=owner)
    written = []
    real_save = default_storage.save

    def deleting_exporter(user):
        DataExport.objects.filter(pk=export.pk).delete()  # e.g. the account was anonymized
        return {}

    def spying_save(name, content, *args, **kwargs):
        written.append(real_save(name, content, *args, **kwargs))
        return written[-1]

    monkeypatch.setitem(privacy._exporters, "vanishing", deleting_exporter)
    monkeypatch.setattr(default_storage, "save", spying_save)
    tasks.build_data_export(export.pk)  # no exception
    assert len(written) == 1
    assert not default_storage.exists(written[0])


def test_build_removes_the_file_when_the_final_save_fails(owner, monkeypatch):
    export = DataExport.objects.create(user=owner)
    written = []
    real_save = default_storage.save

    def spying_save(name, content, *args, **kwargs):
        written.append(real_save(name, content, *args, **kwargs))
        return written[-1]

    def broken_save(self, *args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(default_storage, "save", spying_save)
    monkeypatch.setattr(DataExport, "save", broken_save)
    with pytest.raises(RuntimeError):
        tasks.build_data_export(export.pk)
    monkeypatch.undo()
    assert len(written) == 1
    assert not default_storage.exists(written[0])
    export.refresh_from_db()
    assert export.status == DataExport.Status.FAILED
    assert not export.file


def test_transient_storage_error_leaves_the_export_pending_for_retry(owner, monkeypatch):
    export = DataExport.objects.create(user=owner)

    def flaky(user):
        raise OSError("storage unavailable")

    monkeypatch.setitem(privacy._exporters, "flaky", flaky)
    with pytest.raises(OSError):
        tasks.build_data_export(export.pk)  # called directly: first attempt
    export.refresh_from_db()
    assert export.status == DataExport.Status.PENDING
    assert tasks.build_data_export.max_retries == 3
    assert OSError in tasks.build_data_export.autoretry_for


def test_transient_storage_error_marks_failed_after_the_last_retry(owner, monkeypatch):
    export = DataExport.objects.create(user=owner)

    def flaky(user):
        raise OSError("storage unavailable")

    monkeypatch.setitem(privacy._exporters, "flaky", flaky)
    result = tasks.build_data_export.apply(args=[export.pk], retries=3, throw=False)
    assert isinstance(result.result, OSError)
    export.refresh_from_db()
    assert export.status == DataExport.Status.FAILED


def test_build_ignores_missing_or_already_built_export(owner, django_capture_on_commit_callbacks):
    tasks.build_data_export(999999)
    with django_capture_on_commit_callbacks(execute=True):
        export = services.request_data_export(user=owner)
    export.refresh_from_db()
    name = export.file.name
    tasks.build_data_export(export.pk)
    export.refresh_from_db()
    assert export.file.name == name


def test_build_removes_the_file_when_the_account_was_anonymized_meanwhile(owner, monkeypatch):
    export = DataExport.objects.create(user=owner)
    written = []
    real_save = default_storage.save

    def spying_save(name, content, *args, **kwargs):
        written.append(real_save(name, content, *args, **kwargs))
        return written[-1]

    def anonymizing_exporter(user):
        User.objects.filter(pk=user.pk).update(anonymized_at=timezone.now())
        return {}

    monkeypatch.setitem(privacy._exporters, "anonymizing", anonymizing_exporter)
    monkeypatch.setattr(default_storage, "save", spying_save)
    tasks.build_data_export(export.pk)  # no exception
    assert len(written) == 1
    assert not default_storage.exists(written[0])
    export.refresh_from_db()
    assert export.status == DataExport.Status.PENDING
    assert not export.file


def test_registered_exporter_adds_a_section(owner, monkeypatch):
    monkeypatch.setattr(privacy, "_exporters", dict(privacy._exporters))
    privacy.register_exporter("posts", lambda user: [{"title": f"by {user.public_id}"}])
    data = privacy.build_export(owner)
    assert data["sections"]["posts"] == [{"title": f"by {owner.public_id}"}]


# Export views ---------------------------------------------------------------------


def test_data_export_urls():
    assert reverse("accounts:data_export") == "/me/data-export/"


def test_data_export_page_requires_login(client):
    response = client.get(reverse("accounts:data_export"))
    assert response.status_code == 302


def test_data_export_page_and_request(client, owner, django_capture_on_commit_callbacks):
    client.force_login(owner)
    response = client.get(reverse("accounts:data_export"))
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("accounts:data_export"))
    assert response.status_code == 302
    export = DataExport.objects.get(user=owner)
    response = client.get(reverse("accounts:data_export"))
    assert reverse("accounts:data_export_download", args=[export.public_id]) in (
        response.content.decode()
    )


def test_owner_downloads_ready_export(client, owner, django_capture_on_commit_callbacks):
    export = _ready_export(owner, django_capture_on_commit_callbacks)
    client.force_login(owner)
    response = client.get(reverse("accounts:data_export_download", args=[export.public_id]))
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    assert 'attachment; filename="my-data.json"' in response["Content-Disposition"]
    data = json.loads(b"".join(response.streaming_content))
    assert data["sections"]["account"]["email"] == "owner@example.com"


def test_other_user_cannot_download_export(
    client, owner, other, django_capture_on_commit_callbacks
):
    export = _ready_export(owner, django_capture_on_commit_callbacks)
    client.force_login(other)
    response = client.get(reverse("accounts:data_export_download", args=[export.public_id]))
    assert response.status_code == 404


def test_pending_export_cannot_be_downloaded(client, owner):
    export = DataExport.objects.create(user=owner)
    client.force_login(owner)
    response = client.get(reverse("accounts:data_export_download", args=[export.public_id]))
    assert response.status_code == 404


def test_expired_export_is_404_and_purged(client, owner, django_capture_on_commit_callbacks):
    export = _ready_export(owner, django_capture_on_commit_callbacks)
    fresh = DataExport.objects.create(
        user=owner,
        status=DataExport.Status.READY,
        expires_at=timezone.now() + timedelta(days=1),
    )
    fresh.file.save("fresh.json", ContentFile(b"{}"))
    name = export.file.name
    DataExport.objects.filter(pk=export.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    client.force_login(owner)
    response = client.get(reverse("accounts:data_export_download", args=[export.public_id]))
    assert response.status_code == 404
    assert default_storage.exists(name)
    assert tasks.purge_expired_exports() == 1
    assert not default_storage.exists(name)
    assert not DataExport.objects.filter(pk=export.pk).exists()
    assert DataExport.objects.filter(pk=fresh.pk).exists()
    assert default_storage.exists(fresh.file.name)


# Anonymization --------------------------------------------------------------------


def _deactivated(owner, actor=None):
    services.deactivate_user(actor=actor, user=owner)
    owner.refresh_from_db()
    return owner


def test_active_user_cannot_be_anonymized(owner, functional_admin):
    with pytest.raises(DomainError) as error:
        privacy.anonymize_user(actor=functional_admin, user=owner)
    assert error.value.code == "invalid_status"
    owner.refresh_from_db()
    assert owner.anonymized_at is None
    assert owner.email == "owner@example.com"


def test_anonymize_wipes_personal_data(owner, functional_admin, django_capture_on_commit_callbacks):
    owner.profile.avatar.save("face.png", ContentFile(b"png"))
    avatar_name = owner.profile.avatar.name
    export = _ready_export(owner, django_capture_on_commit_callbacks)
    export_name = export.file.name
    TOTPDevice.objects.create(user=owner, name="phone", confirmed=True)
    StaticDevice.objects.create(user=owner, name="backup")
    session = SessionStore()
    session.create()
    UserSession.objects.create(user=owner, session_key=session.session_key)
    _deactivated(owner, functional_admin)

    with django_capture_on_commit_callbacks(execute=True):
        privacy.anonymize_user(actor=functional_admin, user=owner)

    owner.refresh_from_db()
    assert owner.email == f"anonymized-{owner.public_id}@invalid.invalid"
    assert owner.first_name == owner.last_name == ""
    assert owner.anonymized_at is not None
    assert not owner.has_usable_password()
    with translation.override("en"):
        assert owner.get_full_name() == "Former employee"
    with translation.override("fr"):
        assert owner.get_full_name() == "Ancien collaborateur"
    profile = owner.profile
    assert profile.job_title == profile.bio == profile.language == ""
    assert not profile.avatar
    assert not default_storage.exists(avatar_name)
    assert profile.interests.count() == 0
    assert profile.profile_visibility == profile.Visibility.COMPANY
    assert not Employment.objects.filter(user=owner).exists()
    assert not ExternalIdentity.objects.filter(user=owner).exists()
    assert not UserSession.objects.filter(user=owner).exists()
    assert not DataExport.objects.filter(user=owner).exists()
    assert not default_storage.exists(export_name)
    assert not TOTPDevice.objects.filter(user=owner).exists()
    assert not StaticDevice.objects.filter(user=owner).exists()
    event = AuditEvent.objects.get(action="user.anonymized")
    assert event.actor == functional_admin
    assert event.target_id == str(owner.public_id)
    serialized = json.dumps(event.changes)
    for personal in ("owner@example.com", "Olga", "Owner", "Data engineer"):
        assert personal not in serialized


def test_anonymize_keeps_audit_actor(owner, functional_admin):
    services.request_data_export(user=owner)
    event_ids = list(AuditEvent.objects.filter(actor=owner).values_list("pk", flat=True))
    assert event_ids
    _deactivated(owner, functional_admin)
    privacy.anonymize_user(actor=functional_admin, user=owner)
    assert set(AuditEvent.objects.filter(actor_id=owner.pk).values_list("pk", flat=True)) >= set(
        event_ids
    )


def test_anonymized_user_cannot_log_in(client, owner, functional_admin):
    _deactivated(owner, functional_admin)
    privacy.anonymize_user(actor=functional_admin, user=owner)
    for email in ("owner@example.com", f"anonymized-{owner.public_id}@invalid.invalid"):
        response = client.post(reverse("accounts:login"), {"username": email, "password": PASSWORD})
        assert response.status_code == 200
        assert "_auth_user_id" not in client.session


def test_anonymize_twice_is_a_noop(owner, functional_admin):
    _deactivated(owner, functional_admin)
    privacy.anonymize_user(actor=functional_admin, user=owner)
    owner.refresh_from_db()
    stamp = owner.anonymized_at
    privacy.anonymize_user(actor=functional_admin, user=owner)
    owner.refresh_from_db()
    assert owner.anonymized_at == stamp
    assert AuditEvent.objects.filter(action="user.anonymized").count() == 1


def test_registered_anonymizers_run(owner, functional_admin, monkeypatch):
    monkeypatch.setattr(privacy, "_anonymizers", list(privacy._anonymizers))
    seen = []
    privacy.register_anonymizer(lambda user: seen.append(user.pk))
    _deactivated(owner, functional_admin)
    privacy.anonymize_user(actor=functional_admin, user=owner)
    assert seen == [owner.pk]


def test_functional_admin_cannot_anonymize_protected_account(make_user, functional_admin):
    staff = make_user("staff@example.com", is_staff=True)
    User.objects.filter(pk=staff.pk).update(
        status=User.Status.DEACTIVATED, deactivated_at=timezone.now()
    )
    with pytest.raises(DomainError) as error:
        privacy.anonymize_user(actor=functional_admin, user=staff)
    assert error.value.code == "forbidden_target"


def test_scheduled_task_anonymizes_only_old_deactivations(make_user):
    now = timezone.now()
    days = settings.ACCOUNT_ANONYMIZE_AFTER_DAYS
    assert days == 1095
    old = make_user("old@example.com")
    recent = make_user("recent@example.com")
    active = make_user("active@example.com")
    for user in (old, recent):
        services.deactivate_user(actor=None, user=user)
    User.objects.filter(pk=old.pk).update(deactivated_at=now - timedelta(days=days, hours=1))
    User.objects.filter(pk=recent.pk).update(deactivated_at=now - timedelta(days=days - 1))
    User.objects.filter(pk=active.pk).update(deactivated_at=now - timedelta(days=days + 5))

    assert tasks.anonymize_expired_accounts() == 1

    old.refresh_from_db()
    recent.refresh_from_db()
    active.refresh_from_db()
    assert old.anonymized_at is not None
    assert recent.anonymized_at is None
    assert active.anonymized_at is None
    event = AuditEvent.objects.get(action="user.anonymized")
    assert event.actor is None
    assert tasks.anonymize_expired_accounts() == 0


def test_scheduled_task_carries_on_after_a_failing_account(make_user, monkeypatch):
    days = settings.ACCOUNT_ANONYMIZE_AFTER_DAYS
    broken = make_user("broken@example.com")
    fine = make_user("fine@example.com")
    for user in (broken, fine):
        services.deactivate_user(actor=None, user=user)
    old = timezone.now() - timedelta(days=days, hours=1)
    User.objects.filter(pk__in=[broken.pk, fine.pk]).update(deactivated_at=old)

    def failing(user):
        if user.pk == broken.pk:
            raise RuntimeError("boom for broken@example.com")

    monkeypatch.setattr(privacy, "_anonymizers", list(privacy._anonymizers))
    privacy.register_anonymizer(failing)

    with structlog.testing.capture_logs() as logs:
        assert tasks.anonymize_expired_accounts() == 1

    broken.refresh_from_db()
    fine.refresh_from_db()
    assert broken.anonymized_at is None
    assert broken.email == "broken@example.com"  # rolled back
    assert fine.anonymized_at is not None
    failure = next(log for log in logs if log["event"] == "account_anonymization_failed")
    assert failure["user_public_id"] == str(broken.public_id)
    assert "broken@example.com" not in str(logs)


def test_beat_schedule_has_privacy_tasks():
    schedule = {
        entry["task"]: entry["schedule"] for entry in settings.CELERY_BEAT_SCHEDULE.values()
    }
    purge = schedule["accounts.tasks.purge_expired_exports"]
    anonymize = schedule["accounts.tasks.anonymize_expired_accounts"]
    assert (purge.hour, purge.minute) == ({3}, {30})
    assert (anonymize.hour, anonymize.minute) == ({3}, {45})


# Anonymization view ---------------------------------------------------------------


def test_anonymize_view_requires_manage_permission(client, owner, other):
    _deactivated(owner)
    client.force_login(other)
    url = reverse("manage:user_anonymize", args=[owner.public_id])
    assert client.get(url).status_code == 404
    assert client.post(url).status_code == 404
    owner.refresh_from_db()
    assert owner.anonymized_at is None


def test_anonymize_view_confirm_then_perform(admin_client, owner, functional_admin):
    _deactivated(owner, functional_admin)
    url = reverse("manage:user_anonymize", args=[owner.public_id])
    detail = reverse("manage:user_detail", args=[owner.public_id])
    assert url in admin_client.get(detail).content.decode()
    response = admin_client.get(url)
    assert response.status_code == 200
    assert "owner@example.com" in response.content.decode()
    owner.refresh_from_db()
    assert owner.anonymized_at is None
    response = admin_client.post(url)
    assert response.status_code == 302
    assert response["Location"] == detail
    owner.refresh_from_db()
    assert owner.anonymized_at is not None


def test_anonymize_view_refuses_active_account(admin_client, owner):
    url = reverse("manage:user_anonymize", args=[owner.public_id])
    response = admin_client.post(url, follow=True)
    assert response.redirect_chain == [(reverse("manage:user_detail", args=[owner.public_id]), 302)]
    assert "Only a deactivated account can be anonymized." in response.content.decode()
    owner.refresh_from_db()
    assert owner.anonymized_at is None


def test_anonymize_confirm_page_shows_notice_and_no_form_for_active_account(admin_client, owner):
    url = reverse("manage:user_anonymize", args=[owner.public_id])
    content = admin_client.get(url).content.decode()
    assert "Only a deactivated account can be anonymized." in content
    assert f'action="{url}"' not in content
    assert "Anonymize the account</button>" not in content
    assert "Cancel" in content


def test_anonymize_confirm_page_shows_form_for_deactivated_account(
    admin_client, owner, functional_admin
):
    _deactivated(owner, functional_admin)
    content = admin_client.get(reverse("manage:user_anonymize", args=[owner.public_id])).content
    content = content.decode()
    assert f'action="{reverse("manage:user_anonymize", args=[owner.public_id])}"' in content
    assert "Only a deactivated account can be anonymized." not in content


def test_detail_page_links_to_anonymize_only_for_deactivated_accounts(
    admin_client, owner, functional_admin
):
    url = reverse("manage:user_anonymize", args=[owner.public_id])
    detail = reverse("manage:user_detail", args=[owner.public_id])
    assert url not in admin_client.get(detail).content.decode()
    _deactivated(owner, functional_admin)
    assert url in admin_client.get(detail).content.decode()


def test_can_be_anonymized_predicate(owner, functional_admin):
    assert not privacy.can_be_anonymized(owner)
    _deactivated(owner, functional_admin)
    assert privacy.can_be_anonymized(owner)
    privacy.anonymize_user(actor=functional_admin, user=owner)
    assert not privacy.can_be_anonymized(owner)


def test_anonymize_view_only_accepts_get_and_post(admin_client, client, owner, other):
    url = reverse("manage:user_anonymize", args=[owner.public_id])
    assert admin_client.put(url).status_code == 405
    assert admin_client.delete(url).status_code == 405
    client.force_login(other)
    assert client.put(url).status_code == 404  # non-managers never learn the page exists


def test_anonymize_view_redirects_when_already_anonymized(admin_client, owner, functional_admin):
    _deactivated(owner, functional_admin)
    privacy.anonymize_user(actor=functional_admin, user=owner)
    response = admin_client.get(reverse("manage:user_anonymize", args=[owner.public_id]))
    assert response.status_code == 302


def test_anonymize_view_forbids_protected_account(admin_client, make_user):
    staff = make_user("staff@example.com", is_staff=True)
    User.objects.filter(pk=staff.pk).update(
        status=User.Status.DEACTIVATED, deactivated_at=timezone.now()
    )
    response = admin_client.get(reverse("manage:user_anonymize", args=[staff.public_id]))
    assert response.status_code == 403


def test_anonymize_erases_login_records_holding_the_email(owner, other, functional_admin):
    from axes.models import AccessAttempt, AccessFailureLog, AccessLog

    for model in (AccessAttempt, AccessFailureLog, AccessLog):
        for email in ("owner@example.com", "other@example.com"):
            extra = {"failures_since_start": 1} if model is AccessAttempt else {}
            model.objects.create(username=email, ip_address="10.0.0.1", user_agent="ua", **extra)
    _deactivated(owner, functional_admin)
    privacy.anonymize_user(actor=functional_admin, user=owner)
    for model in (AccessAttempt, AccessFailureLog, AccessLog):
        assert not model.objects.filter(username="owner@example.com").exists()
        assert model.objects.filter(username="other@example.com").exists()


def test_purge_removes_stale_unfinished_exports(owner):
    stale = DataExport.objects.create(user=owner)
    failed = DataExport.objects.create(user=owner, status=DataExport.Status.FAILED)
    recent = DataExport.objects.create(user=owner)
    old = timezone.now() - timedelta(days=settings.DATA_EXPORT_TTL_DAYS, minutes=1)
    DataExport.objects.filter(pk__in=[stale.pk, failed.pk]).update(created_at=old)
    assert tasks.purge_expired_exports() == 2
    assert list(DataExport.objects.values_list("pk", flat=True)) == [recent.pk]


def test_discarding_a_stored_file_never_raises(monkeypatch):
    class BrokenStorage:
        def delete(self, name):
            raise OSError("storage unavailable")

    tasks._discard_stored_file(BrokenStorage(), "exports/x.json")


def _wait_for_a_lock_waiter(timeout: float = 10.0) -> None:
    """Return once another connection of the test database waits for a row lock."""
    deadline = threading.Event()
    timer = threading.Timer(timeout, deadline.set)
    timer.start()
    try:
        with connection.cursor() as cursor:
            while not deadline.is_set():
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
                if cursor.fetchone()[0]:
                    return
                deadline.wait(0.01)
    finally:
        timer.cancel()
    raise AssertionError("no connection is waiting for a lock")


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_anonymize_waits_for_a_build_saving_its_file_and_deletes_that_file(make_user):
    """A build commits its file while anonymize_user runs: the file must not survive."""
    user = make_user("race@example.com", status=User.Status.DEACTIVATED)
    export = DataExport.objects.create(user=user)
    name = default_storage.save(f"exports/{export.public_id}.json", ContentFile(b"{}"))
    saved, go = threading.Event(), threading.Event()
    errors = []

    def build_commits_its_file():
        # The final step of build_data_export, held open until anonymize_user waits.
        try:
            with transaction.atomic():
                row = DataExport.objects.select_for_update().get(pk=export.pk)
                row.file.name = name
                row.status = DataExport.Status.READY
                row.save(update_fields=["file", "status"])
                saved.set()
                assert go.wait(10)
        except Exception as error:  # reported by the main thread
            errors.append(error)
        finally:
            saved.set()
            connection.close()

    def anonymize():
        try:
            privacy.anonymize_user(actor=None, user=User.objects.get(pk=user.pk))
        except Exception as error:  # reported by the main thread
            errors.append(error)
        finally:
            connection.close()

    builder = threading.Thread(target=build_commits_its_file)
    anonymizer = threading.Thread(target=anonymize)
    builder.start()
    assert saved.wait(10)
    anonymizer.start()
    try:
        _wait_for_a_lock_waiter()
    finally:
        go.set()
        builder.join(10)
        anonymizer.join(10)

    assert not builder.is_alive() and not anonymizer.is_alive()
    assert errors == []
    assert not DataExport.objects.filter(pk=export.pk).exists()
    assert not default_storage.exists(name)
    assert User.objects.get(pk=user.pk).anonymized_at is not None
