import hashlib
import hmac
from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser, Group
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent, AuditImmutableError
from audit.services import record
from audit.tasks import purge_audit_events
from communities.models import Community, CommunityCategory
from core.context import bind_request, clear_request, set_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return User.objects.create_user(email="a@example.com", password="x")


@pytest.fixture
def request_ctx(settings):
    settings.NUM_PROXIES = 0
    request = RequestFactory().get("/", REMOTE_ADDR="203.0.113.9")
    request.request_id = "req-12345678"
    bind_request(request)
    yield
    clear_request()


def _hash(ip):
    return hmac.new(b"test-only-audit-key", ip.encode(), hashlib.sha256).hexdigest()


def test_record_stores_request_id_and_hashed_ip(user, request_ctx, settings):
    settings.AUDIT_IP_HASH_KEY = "test-only-audit-key"
    event = record(actor=user, action="user.created", target=user)
    event.refresh_from_db()
    assert event.request_id == "req-12345678"
    assert event.ip_hash == _hash("203.0.113.9")
    assert "203.0.113.9" not in event.ip_hash
    assert event.target_type == "accounts.user"
    assert event.target_id == str(user.public_id)
    assert event.actor == user


def test_record_with_one_proxy_uses_rightmost_forwarded_address(settings):
    settings.NUM_PROXIES = 1
    settings.AUDIT_IP_HASH_KEY = "test-only-audit-key"
    request = RequestFactory().get(
        "/", REMOTE_ADDR="10.0.0.1", headers={"X-Forwarded-For": "6.6.6.6, 198.51.100.7"}
    )
    request.request_id = "req-12345678"
    bind_request(request)
    try:
        event = record(actor=None, action="x.y", target=("thing", "1"))
    finally:
        clear_request()
    assert event.ip_hash == _hash("198.51.100.7")


def test_record_without_request_context_has_blank_ip_and_request_id():
    event = record(actor=None, action="x.y", target=("thing", "1"))
    assert event.ip_hash == ""
    assert event.request_id == ""
    assert (event.target_type, event.target_id) == ("thing", "1")


def test_anonymous_actor_is_stored_as_none():
    event = record(actor=AnonymousUser(), action="x.y", target=("thing", "1"))
    assert event.actor is None


def test_community_id_and_changes_are_stored():
    community = Community.objects.create(
        name="C", slug="c", tagline="t", category=CommunityCategory.objects.first()
    )
    event = record(
        actor=None, action="x.y", target=("thing", "1"), changes={"a": 1}, community_id=community.pk
    )
    event.refresh_from_db()
    assert event.community_id == community.pk
    assert event.changes == {"a": 1}


def test_sensitive_keys_are_removed_from_changes():
    event = record(
        actor=None,
        action="x.y",
        target=("thing", "1"),
        changes={
            "password": "x",
            "email": "a@b",
            "api_token": "t",
            "Client_Secret": "s",
            "otp_code": "1",
            "nested": {"password": "p", "keep": 1},
        },
    )
    assert event.changes == {"email": "a@b", "nested": {"keep": 1}}


def test_saving_existing_event_raises():
    event = record(actor=None, action="x.y", target=("thing", "1"))
    event.action = "other"
    with pytest.raises(AuditImmutableError):
        event.save()


def test_deleting_instance_raises():
    event = record(actor=None, action="x.y", target=("thing", "1"))
    with pytest.raises(AuditImmutableError):
        event.delete()


def test_queryset_update_and_delete_raise():
    record(actor=None, action="x.y", target=("thing", "1"))
    with pytest.raises(AuditImmutableError):
        AuditEvent.objects.all().update(action="z")
    with pytest.raises(AuditImmutableError):
        AuditEvent.objects.all().delete()
    assert AuditEvent.objects.count() == 1


def test_adding_user_to_group_records_roles_changed(user):
    group = Group.objects.get(name="employee")
    user.groups.add(group)
    event = AuditEvent.objects.get(action="user.roles_changed")
    assert event.changes == {"added": ["employee"]}
    assert event.target_id == str(user.public_id)
    assert event.actor is None


def test_removing_and_clearing_groups_record_events(user):
    group = Group.objects.get(name="employee")
    user.groups.add(group)
    user.groups.remove(group)
    user.groups.add(group)
    user.groups.clear()
    changes = [
        e.changes for e in AuditEvent.objects.filter(action="user.roles_changed").order_by("id")
    ]
    assert changes == [
        {"added": ["employee"]},
        {"removed": ["employee"]},
        {"added": ["employee"]},
        {"removed": ["employee"]},
    ]


def test_reverse_side_group_add_records_event(user):
    group = Group.objects.get(name="auditor")
    group.user_set.add(user)
    event = AuditEvent.objects.get(action="user.roles_changed")
    assert event.changes == {"added": ["auditor"]}
    assert event.target_id == str(user.public_id)


def test_roles_changed_actor_comes_from_request_context(user, request_ctx):
    admin = User.objects.create_user(email="adm@example.com", password="x")
    set_user(admin)
    user.groups.add(Group.objects.get(name="employee"))
    event = AuditEvent.objects.get(action="user.roles_changed")
    assert event.actor == admin


def test_purge_removes_only_events_older_than_retention(settings):
    settings.AUDIT_RETENTION_DAYS = 30
    AuditEvent.objects.create(
        action="old",
        target_type="t",
        target_id="1",
        created_at=timezone.now() - timedelta(days=31),
    )
    recent = record(actor=None, action="recent", target=("t", "1"))
    assert purge_audit_events() == 1
    assert list(AuditEvent.objects.values_list("action", flat=True)) == ["recent"]
    assert recent.pk


def test_purge_older_than_returns_count():
    record(actor=None, action="a", target=("t", "1"))
    assert AuditEvent.purge_older_than(timezone.now() + timedelta(days=1)) == 1


def test_admin_is_read_only(rf):
    from django.contrib.admin import AdminSite

    from audit.admin import AuditEventAdmin

    model_admin = AuditEventAdmin(AuditEvent, AdminSite())
    request = rf.get("/")
    request.user = User(is_superuser=True, is_staff=True)
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)


def test_target_without_public_id_uses_pk():
    group = Group.objects.get(name="employee")
    event = record(actor=None, action="x.y", target=group)
    assert event.target_type == "auth.group"
    assert event.target_id == str(group.pk)


def test_saving_new_instance_with_existing_pk_raises_integrity_error():
    event = record(actor=None, action="orig", target=("t", "1"))
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEvent(pk=event.pk, action="forged", target_type="t", target_id="1").save()
    assert AuditEvent.objects.get(pk=event.pk).action == "orig"


def test_removing_unheld_group_records_nothing(user):
    user.groups.remove(Group.objects.get(name="auditor"))
    assert not AuditEvent.objects.filter(action="user.roles_changed").exists()


def test_removing_held_and_unheld_groups_lists_only_held(user):
    user.groups.add(Group.objects.get(name="employee"))
    user.groups.remove(*Group.objects.filter(name__in=["employee", "auditor"]))
    event = AuditEvent.objects.filter(action="user.roles_changed").order_by("-id").first()
    assert event.changes == {"removed": ["employee"]}


def test_reverse_remove_records_only_users_who_held_group():
    holder = User.objects.create_user(email="h@example.com", password="x")
    other = User.objects.create_user(email="o@example.com", password="x")
    group = Group.objects.get(name="auditor")
    holder.groups.add(group)
    group.user_set.remove(holder, other)
    events = AuditEvent.objects.filter(
        action="user.roles_changed", changes={"removed": ["auditor"]}
    )
    assert [e.target_id for e in events] == [str(holder.public_id)]


def test_adding_already_held_group_records_nothing(user):
    group = Group.objects.get(name="employee")
    user.groups.add(group)
    user.groups.add(group)
    assert AuditEvent.objects.filter(action="user.roles_changed").count() == 1
