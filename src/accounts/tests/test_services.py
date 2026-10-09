import pytest
from django.contrib.auth.models import Group
from django.test import Client

from accounts.models import User
from accounts.roles import Role, user_roles
from accounts.services import (
    create_user,
    deactivate_user,
    reactivate_user,
    resend_activation,
    set_roles,
    suspend_user,
)
from audit.models import AuditEvent
from core.errors import DomainError
from organizations.models import OrganizationUnit

pytestmark = pytest.mark.django_db


@pytest.fixture
def unit():
    return OrganizationUnit.objects.create(code="ENG", name="Engineering")


@pytest.fixture
def superuser(make_user):
    return make_user("root@example.com", is_superuser=True, is_staff=True)


def events(action):
    return list(AuditEvent.objects.filter(action=action).order_by("id"))


# create_user ----------------------------------------------------------------------


def test_create_user_is_pending_with_unusable_password_and_audited(
    functional_admin, mailoutbox, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        user = create_user(
            actor=functional_admin, email="New.Person@Example.com", first_name="New", last_name="P"
        )
    assert user.email == "new.person@example.com"
    assert user.status == User.Status.PENDING
    assert not user.has_usable_password()
    assert user_roles(user) == {Role.EMPLOYEE.value}
    assert len(callbacks) == 1
    assert [m.to for m in mailoutbox] == [["new.person@example.com"]]
    [event] = events("user.created")
    assert event.actor == functional_admin
    assert event.target_id == str(user.public_id)
    assert event.changes["roles"] == ["employee"]
    # Roles granted at creation are part of user.created, not a separate event.
    assert not AuditEvent.objects.filter(
        action="user.roles_changed", target_id=str(user.public_id)
    ).exists()


def test_create_user_sends_no_email_before_commit(functional_admin, mailoutbox):
    create_user(actor=functional_admin, email="x@example.com", first_name="X", last_name="Y")
    assert mailoutbox == []


def test_create_user_without_activation_email(
    functional_admin, mailoutbox, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        create_user(
            actor=functional_admin,
            email="x@example.com",
            first_name="X",
            last_name="Y",
            send_activation=False,
        )
    assert mailoutbox == []


def test_create_user_with_unit_manager_and_roles(functional_admin, unit, make_user):
    manager = make_user("boss@example.com")
    user = create_user(
        actor=functional_admin,
        email="x@example.com",
        first_name="X",
        last_name="Y",
        unit=unit,
        manager=manager,
        roles=["employee", "community_creator"],
    )
    assert user.employment.unit == unit
    assert user.employment.manager == manager
    assert user_roles(user) == {"employee", "community_creator"}


def test_create_user_rejects_duplicate_email_case_insensitively(functional_admin, make_user):
    make_user("taken@example.com")
    with pytest.raises(DomainError) as exc:
        create_user(
            actor=functional_admin, email="TAKEN@example.com", first_name="a", last_name="b"
        )
    assert exc.value.code == "email_taken"
    assert exc.value.message
    assert not events("user.created")


def test_create_user_manager_requires_unit(functional_admin, make_user):
    manager = make_user("boss@example.com")
    with pytest.raises(DomainError) as exc:
        create_user(
            actor=functional_admin,
            email="x@example.com",
            first_name="X",
            last_name="Y",
            manager=manager,
        )
    assert exc.value.code == "manager_requires_unit"


def test_functional_admin_cannot_create_technical_admin(functional_admin):
    with pytest.raises(DomainError) as exc:
        create_user(
            actor=functional_admin,
            email="x@example.com",
            first_name="X",
            last_name="Y",
            roles=["technical_admin"],
        )
    assert exc.value.code == "forbidden_role"
    assert not User.objects.filter(email="x@example.com").exists()


# Status changes -------------------------------------------------------------------


def test_suspend_user_ends_sessions_and_audits(functional_admin, make_user):
    target = make_user("bob@example.com")
    other = Client()
    other.force_login(target)
    assert target.tracked_sessions.count() == 1
    suspend_user(actor=functional_admin, user=target)
    target.refresh_from_db()
    assert target.status == User.Status.SUSPENDED
    assert not target.tracked_sessions.exists()
    assert not other.get("/").wsgi_request.user.is_authenticated
    [event] = events("user.suspended")
    assert event.changes == {"status": ["active", "suspended"]}
    assert event.actor == functional_admin


def test_reactivate_suspended_user(functional_admin, make_user):
    target = make_user("bob@example.com", status=User.Status.SUSPENDED)
    reactivate_user(actor=functional_admin, user=target)
    target.refresh_from_db()
    assert target.status == User.Status.ACTIVE
    assert events("user.reactivated")[0].changes == {"status": ["suspended", "active"]}


@pytest.mark.parametrize("status", [User.Status.ACTIVE, User.Status.PENDING])
def test_reactivate_requires_suspended(functional_admin, make_user, status):
    target = make_user("bob@example.com", status=status)
    with pytest.raises(DomainError) as exc:
        reactivate_user(actor=functional_admin, user=target)
    assert exc.value.code == "invalid_status"
    assert not events("user.reactivated")


@pytest.mark.parametrize("status", [User.Status.PENDING, User.Status.SUSPENDED])
def test_suspend_requires_active(functional_admin, make_user, status):
    target = make_user("bob@example.com", status=status)
    with pytest.raises(DomainError) as exc:
        suspend_user(actor=functional_admin, user=target)
    assert exc.value.code == "invalid_status"


@pytest.mark.parametrize("status", [User.Status.PENDING, User.Status.ACTIVE, User.Status.SUSPENDED])
def test_deactivate_from_any_status(functional_admin, make_user, status):
    target = make_user("bob@example.com", status=status)
    other = Client()
    other.force_login(target)
    deactivate_user(actor=functional_admin, user=target)
    target.refresh_from_db()
    assert target.status == User.Status.DEACTIVATED
    assert target.deactivated_at is not None
    assert not target.tracked_sessions.exists()
    assert events("user.deactivated")[0].changes == {"status": [status.value, "deactivated"]}


def test_deactivate_twice_is_refused(functional_admin, make_user):
    target = make_user("bob@example.com", status=User.Status.DEACTIVATED)
    with pytest.raises(DomainError) as exc:
        deactivate_user(actor=functional_admin, user=target)
    assert exc.value.code == "invalid_status"


@pytest.mark.parametrize("service", [suspend_user, deactivate_user])
def test_actor_cannot_act_on_themselves(functional_admin, service):
    with pytest.raises(DomainError) as exc:
        service(actor=functional_admin, user=functional_admin)
    assert exc.value.code == "self_action"
    functional_admin.refresh_from_db()
    assert functional_admin.status == User.Status.ACTIVE


def test_resend_activation_for_pending_user(
    functional_admin, make_user, mailoutbox, django_capture_on_commit_callbacks
):
    target = make_user("bob@example.com", status=User.Status.PENDING)
    with django_capture_on_commit_callbacks(execute=True):
        resend_activation(actor=functional_admin, user=target)
    assert [m.to for m in mailoutbox] == [["bob@example.com"]]
    assert events("user.activation_resent")[0].changes == {"status": ["pending", "pending"]}


def test_resend_activation_refused_for_active_user(functional_admin, make_user, mailoutbox):
    target = make_user("bob@example.com")
    with pytest.raises(DomainError) as exc:
        resend_activation(actor=functional_admin, user=target)
    assert exc.value.code == "invalid_status"


# Roles ----------------------------------------------------------------------------


def test_set_roles_writes_exactly_one_event_with_before_and_after(functional_admin, make_user):
    target = make_user("bob@example.com")
    target.groups.add(Group.objects.get(name=Role.EMPLOYEE))
    before = AuditEvent.objects.filter(action="user.roles_changed").count()
    set_roles(actor=functional_admin, user=target, roles=["community_creator", "auditor"])
    changed = AuditEvent.objects.filter(action="user.roles_changed").order_by("id")[before:]
    assert len(changed) == 1
    assert changed[0].changes == {"before": ["employee"], "after": ["auditor", "community_creator"]}
    assert changed[0].actor == functional_admin
    assert user_roles(target) == {"auditor", "community_creator"}


def test_set_roles_leaves_other_groups_untouched(functional_admin, make_user):
    target = make_user("bob@example.com")
    other = Group.objects.create(name="beta_testers")
    target.groups.add(other, Group.objects.get(name=Role.EMPLOYEE))
    set_roles(actor=functional_admin, user=target, roles=["auditor"])
    assert set(target.groups.values_list("name", flat=True)) == {"beta_testers", "auditor"}


def test_set_roles_rejects_unknown_role(functional_admin, make_user):
    target = make_user("bob@example.com")
    with pytest.raises(DomainError) as exc:
        set_roles(actor=functional_admin, user=target, roles=["beta_testers"])
    assert exc.value.code == "unknown_role"


def test_functional_admin_cannot_grant_technical_admin(functional_admin, make_user):
    target = make_user("bob@example.com")
    with pytest.raises(DomainError) as exc:
        set_roles(actor=functional_admin, user=target, roles=["employee", "technical_admin"])
    assert exc.value.code == "forbidden_role"
    assert "technical_admin" not in user_roles(User.objects.get(pk=target.pk))
    assert not AuditEvent.objects.filter(
        action="user.roles_changed", target_id=str(target.public_id)
    ).exists()


def test_functional_admin_cannot_revoke_technical_admin(functional_admin, make_user):
    target = make_user("bob@example.com")
    target.groups.add(Group.objects.get(name=Role.TECHNICAL_ADMIN))
    with pytest.raises(DomainError) as exc:
        set_roles(actor=functional_admin, user=target, roles=["employee"])
    assert exc.value.code == "forbidden_role"


def test_functional_admin_may_keep_existing_technical_admin(functional_admin, make_user):
    target = make_user("bob@example.com")
    target.groups.add(Group.objects.get(name=Role.TECHNICAL_ADMIN))
    set_roles(actor=functional_admin, user=target, roles=["technical_admin", "auditor"])
    assert user_roles(User.objects.get(pk=target.pk)) == {"technical_admin", "auditor"}


def test_superuser_can_grant_technical_admin(superuser, make_user):
    target = make_user("bob@example.com")
    set_roles(actor=superuser, user=target, roles=["technical_admin"])
    assert user_roles(User.objects.get(pk=target.pk)) == {"technical_admin"}


def test_m2m_audit_still_works_outside_the_service(make_user):
    target = make_user("bob@example.com")
    target.groups.add(Group.objects.get(name=Role.AUDITOR))
    assert events("user.roles_changed")[0].changes == {"added": ["auditor"]}


def test_set_roles_without_change_records_nothing(functional_admin, make_user):
    target = make_user("bob@example.com")
    target.groups.add(Group.objects.get(name=Role.EMPLOYEE))
    count = AuditEvent.objects.count()
    set_roles(actor=functional_admin, user=target, roles=["employee"])
    assert AuditEvent.objects.count() == count
