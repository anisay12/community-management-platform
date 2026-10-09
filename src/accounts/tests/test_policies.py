import pytest
from django.contrib.auth.models import AnonymousUser, Group

from accounts.models import User
from accounts.policies import can_administer_user, can_manage_users
from accounts.roles import Role

pytestmark = pytest.mark.django_db


def test_functional_admin_can_manage_users(functional_admin):
    assert can_manage_users(functional_admin)


def test_superuser_can_manage_users(make_user):
    assert can_manage_users(make_user("root@example.com", is_superuser=True))


@pytest.mark.parametrize(
    "role", [Role.EMPLOYEE, Role.COMMUNITY_CREATOR, Role.TECHNICAL_ADMIN, Role.AUDITOR]
)
def test_other_roles_cannot_manage_users(make_user, role):
    user = make_user("someone@example.com")
    user.groups.add(Group.objects.get(name=role))
    assert not can_manage_users(user)


def test_anonymous_cannot_manage_users():
    assert not can_manage_users(AnonymousUser())


@pytest.mark.parametrize("status", [User.Status.SUSPENDED, User.Status.DEACTIVATED])
def test_inactive_functional_admin_cannot_manage_users(make_user, status):
    user = make_user("fa@example.com", status=status)
    user.groups.add(Group.objects.get(name=Role.FUNCTIONAL_ADMIN))
    assert not can_manage_users(user)


@pytest.fixture
def superuser(make_user):
    return make_user("root@example.com", is_superuser=True)


def make_target(make_user, kind):
    target = make_user(
        "target@example.com", is_superuser=kind == "superuser", is_staff=kind == "staff"
    )
    if kind == "technical_admin":
        target.groups.add(Group.objects.get(name=Role.TECHNICAL_ADMIN))
    return target


@pytest.mark.parametrize("kind", ["superuser", "staff", "technical_admin"])
def test_functional_admin_cannot_administer_protected_accounts(functional_admin, make_user, kind):
    assert not can_administer_user(functional_admin, make_target(make_user, kind))


@pytest.mark.parametrize("kind", ["superuser", "staff", "technical_admin", "employee"])
def test_superuser_can_administer_every_account(superuser, make_user, kind):
    assert can_administer_user(superuser, make_target(make_user, kind))


def test_functional_admin_can_administer_ordinary_accounts(functional_admin, make_user):
    assert can_administer_user(functional_admin, make_target(make_user, "employee"))


def test_non_manager_cannot_administer_anyone(make_user):
    actor = make_user("emp@example.com")
    target = make_target(make_user, "employee")
    assert not can_administer_user(actor, target)
    assert not can_administer_user(AnonymousUser(), target)
