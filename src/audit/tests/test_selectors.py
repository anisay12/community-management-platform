from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth.models import AnonymousUser, Group
from django.utils import timezone

from accounts.models import User
from accounts.roles import Role
from audit.models import AuditEvent
from audit.policies import (
    audit_scopes,
    can_view_audit_log,
    is_technical_action,
)
from audit.selectors import action_choices, events_visible_to, filter_events

pytestmark = pytest.mark.django_db


def _user(make_user, email, *roles, **extra):
    user = make_user(email, **extra)
    for role in roles:
        user.groups.add(Group.objects.get(name=role))
    return user


def _reset():
    """Drop the events recorded as a side effect of creating users and granting roles."""
    AuditEvent.purge_older_than(timezone.now() + timedelta(days=1))


def _make_events(make_user):
    actor = make_user("actor@example.com", first_name="Alice", last_name="Martin")
    other = make_user("other@example.com", first_name="Bob", last_name="Durand")
    _reset()
    base = timezone.now()
    rows = {
        "login": AuditEvent.objects.create(
            actor=actor, action="auth.login", target_type="user", target_id="1", created_at=base
        ),
        "failed": AuditEvent.objects.create(
            actor=None,
            action="auth.login_failed",
            target_type="user",
            target_id="2",
            created_at=base - timedelta(minutes=1),
        ),
        "status": AuditEvent.objects.create(
            actor=other,
            action="user.status_changed",
            target_type="user",
            target_id="42",
            created_at=base - timedelta(minutes=2),
        ),
        "profile": AuditEvent.objects.create(
            actor=actor,
            action="profile.updated",
            target_type="profile",
            target_id="7",
            created_at=base - timedelta(minutes=3),
        ),
    }
    return rows


@pytest.fixture
def events(make_user):
    return _make_events(make_user)


def _actions(qs):
    return {e.action for e in qs}


def test_is_technical_action():
    assert is_technical_action("auth.login")
    assert not is_technical_action("user.created")
    assert not is_technical_action("authx.thing")


def test_no_scope_for_unprivileged_users(make_user):
    employee = _user(make_user, "e@example.com", Role.EMPLOYEE)
    creator = _user(make_user, "c@example.com", Role.COMMUNITY_CREATOR)
    suspended = _user(make_user, "s@example.com", Role.AUDITOR, status=User.Status.SUSPENDED)
    for user in (employee, creator, suspended, AnonymousUser()):
        assert audit_scopes(user) == frozenset()
        assert can_view_audit_log(user) is False
        assert not events_visible_to(user).exists()


def test_scopes_per_role(make_user):
    assert audit_scopes(_user(make_user, "a@example.com", Role.AUDITOR)) == {
        "functional",
        "technical",
    }
    root = make_user("root@example.com", is_superuser=True)
    assert audit_scopes(root) == {"functional", "technical"}
    tech = _user(make_user, "t@example.com", Role.TECHNICAL_ADMIN)
    assert audit_scopes(tech) == {"technical"}
    func = _user(make_user, "f@example.com", Role.FUNCTIONAL_ADMIN)
    assert audit_scopes(func) == {"functional"}
    both = _user(make_user, "b@example.com", Role.FUNCTIONAL_ADMIN, Role.TECHNICAL_ADMIN)
    assert audit_scopes(both) == {"functional", "technical"}
    assert all(can_view_audit_log(u) for u in (tech, func, both, root))


def test_visible_events_by_role(make_user):
    auditor = _user(make_user, "a@example.com", Role.AUDITOR)
    root = make_user("root@example.com", is_superuser=True)
    tech = _user(make_user, "t@example.com", Role.TECHNICAL_ADMIN)
    func = _user(make_user, "f@example.com", Role.FUNCTIONAL_ADMIN)
    both = _user(make_user, "b@example.com", Role.FUNCTIONAL_ADMIN, Role.TECHNICAL_ADMIN)
    _make_events(make_user)
    everything = {"auth.login", "auth.login_failed", "user.status_changed", "profile.updated"}
    assert _actions(events_visible_to(auditor)) == everything
    assert _actions(events_visible_to(root)) == everything
    assert _actions(events_visible_to(both)) == everything
    assert _actions(events_visible_to(tech)) == {"auth.login", "auth.login_failed"}
    assert _actions(events_visible_to(func)) == {"user.status_changed", "profile.updated"}


def test_visible_events_ordered_newest_first_with_pk_tiebreak(make_user):
    auditor = _user(make_user, "a@example.com", Role.AUDITOR)
    _reset()
    now = timezone.now()
    first = AuditEvent.objects.create(
        action="user.a", target_type="u", target_id="1", created_at=now
    )
    second = AuditEvent.objects.create(
        action="user.b", target_type="u", target_id="1", created_at=now
    )
    older = AuditEvent.objects.create(
        action="user.c", target_type="u", target_id="1", created_at=now - timedelta(days=1)
    )
    assert list(events_visible_to(auditor)) == [second, first, older]


def test_visible_events_select_related_actor(make_user, django_assert_num_queries):
    auditor = _user(make_user, "a@example.com", Role.AUDITOR)
    _make_events(make_user)
    qs = events_visible_to(auditor)  # resolves the viewer's roles, outside the count
    with django_assert_num_queries(1):
        names = [e.actor.email if e.actor else None for e in qs]
    assert len(names) == 4


def test_filter_action_exact(events):
    qs = AuditEvent.objects.all()
    assert _actions(filter_events(qs, action="auth.login")) == {"auth.login"}


def test_filter_actor_substring_email_first_last_name(events):
    qs = AuditEvent.objects.all()
    assert _actions(filter_events(qs, actor="ACTOR@")) == {"auth.login", "profile.updated"}
    assert _actions(filter_events(qs, actor="mart")) == {"auth.login", "profile.updated"}
    assert _actions(filter_events(qs, actor="ALI")) == {"auth.login", "profile.updated"}
    assert _actions(filter_events(qs, actor="bob")) == {"user.status_changed"}
    assert _actions(filter_events(qs, actor="duRAND")) == {"user.status_changed"}
    assert filter_events(qs, actor="nobody").count() == 0


def test_filter_target(events):
    qs = AuditEvent.objects.all()
    assert _actions(filter_events(qs, target="42")) == {"user.status_changed"}
    assert _actions(filter_events(qs, target="PROFILE")) == {"profile.updated"}
    # target_type is an exact match, not a substring.
    assert filter_events(qs, target="prof").count() == 0
    assert _actions(filter_events(qs, target="user")) == {
        "auth.login",
        "auth.login_failed",
        "user.status_changed",
    }


def test_filter_dates_inclusive_in_current_time_zone(settings):
    settings.TIME_ZONE = "Europe/Paris"
    tz = ZoneInfo("Europe/Paris")
    stamps = {
        "before": datetime(2026, 3, 9, 23, 59, tzinfo=tz),
        "start": datetime(2026, 3, 10, 0, 0, tzinfo=tz),
        "late": datetime(2026, 3, 10, 23, 59, tzinfo=tz),
        "after": datetime(2026, 3, 11, 0, 0, tzinfo=tz),
    }
    for name, stamp in stamps.items():
        AuditEvent.objects.create(
            action=f"user.{name}", target_type="u", target_id="1", created_at=stamp
        )
    qs = AuditEvent.objects.all()
    day = date(2026, 3, 10)
    assert _actions(filter_events(qs, date_from=day, date_to=day)) == {"user.start", "user.late"}
    assert _actions(filter_events(qs, date_from=day)) == {"user.start", "user.late", "user.after"}
    assert _actions(filter_events(qs, date_to=day)) == {"user.before", "user.start", "user.late"}


def test_filter_dates_follow_active_time_zone():
    # 23:30 UTC on the 10th is already the 11th in Paris (UTC+1 in January).
    AuditEvent.objects.create(
        action="user.x",
        target_type="u",
        target_id="1",
        created_at=datetime(2026, 1, 10, 23, 30, tzinfo=UTC),
    )
    qs = AuditEvent.objects.all()
    with timezone.override(ZoneInfo("Europe/Paris")):
        assert filter_events(qs, date_from=date(2026, 1, 11)).count() == 1
        assert filter_events(qs, date_to=date(2026, 1, 10)).count() == 0
    with timezone.override(UTC):
        assert filter_events(qs, date_to=date(2026, 1, 10)).count() == 1


def test_blank_filters_return_input_unchanged(events):
    qs = AuditEvent.objects.all()
    result = filter_events(qs, action="", actor="", target="", date_from=None, date_to=None)
    assert result is qs


def test_action_choices_sorted_distinct_and_scoped(make_user):
    func = _user(make_user, "f@example.com", Role.FUNCTIONAL_ADMIN)
    auditor = _user(make_user, "a@example.com", Role.AUDITOR)
    _make_events(make_user)
    AuditEvent.objects.create(action="user.status_changed", target_type="u", target_id="9")
    assert action_choices(events_visible_to(func)) == ["profile.updated", "user.status_changed"]
    assert not any(a.startswith("auth.") for a in action_choices(events_visible_to(func)))
    assert action_choices(events_visible_to(auditor)) == [
        "auth.login",
        "auth.login_failed",
        "profile.updated",
        "user.status_changed",
    ]
