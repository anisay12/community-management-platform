import pytest
from django.contrib.auth.models import AnonymousUser, Group
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from accounts.roles import Role
from communities import policies
from communities.models import Community
from communities.roles import CommunityRole

pytestmark = pytest.mark.django_db

MANAGE = {
    "can_manage_members": {"member", "moderator"},
    "can_change_roles": {"member", "moderator", "animator"},
    "can_configure": {"member", "moderator", "animator"},
    "can_suspend": {"member", "moderator", "animator", "owner"},
    "can_archive": {"member", "moderator", "animator"},
}


@pytest.mark.parametrize("policy", list(MANAGE))
@pytest.mark.parametrize("role", ["member", "moderator", "animator", "owner"])
def test_management_policies_by_role(
    policy, role, make_community, make_user, add_member, functional_admin
):
    community = make_community()
    user = make_user()
    add_member(community, user, role=role)
    check = getattr(policies, policy)
    assert check(user, community) is (role not in MANAGE[policy])
    assert check(functional_admin, community) is True
    assert check(make_user("outsider@example.com"), community) is False
    assert check(AnonymousUser(), community) is False


def test_suspended_owner_loses_every_right(make_community, make_user, add_member):
    community = make_community()
    owner = make_user()
    add_member(community, owner, role=CommunityRole.OWNER)
    owner.status = User.Status.SUSPENDED
    owner.save()
    owner.refresh_from_db()
    for name in MANAGE:
        assert getattr(policies, name)(owner, community) is False
    assert policies.membership_of(owner, community) is None


def test_suspended_functional_admin_has_no_rights(make_community, functional_admin):
    functional_admin.status = User.Status.SUSPENDED
    functional_admin.save()
    functional_admin.refresh_from_db()
    community = make_community()
    assert not policies.can_suspend(functional_admin, community)
    assert not policies.can_manage_categories(functional_admin)
    assert not policies.can_create_community(functional_admin)


@pytest.mark.parametrize(
    ("access_mode", "status", "expected"),
    [
        (Community.AccessMode.OPEN, Community.Status.ACTIVE, True),
        (Community.AccessMode.REQUEST, Community.Status.ACTIVE, True),
        (Community.AccessMode.INVITE, Community.Status.ACTIVE, False),
        (Community.AccessMode.OPEN, Community.Status.SUSPENDED, False),
        (Community.AccessMode.OPEN, Community.Status.ARCHIVED, False),
    ],
)
def test_can_join(access_mode, status, expected, make_community, make_user, add_member):
    community = make_community(access_mode=access_mode, status=status)
    user = make_user()
    assert policies.can_join(user, community) is expected
    assert policies.can_join(AnonymousUser(), community) is False
    add_member(community, user)
    policies.clear_membership_cache(user)
    assert policies.can_join(user, community) is False


def test_create_and_category_policies(make_user, functional_admin):
    employee = make_user("employee@example.com")
    creator = make_user("creator@example.com")
    creator.groups.add(Group.objects.get(name=Role.COMMUNITY_CREATOR))
    root = make_user("root@example.com", is_superuser=True)
    assert not policies.can_create_community(employee)
    assert policies.can_create_community(creator)
    assert policies.can_create_community(functional_admin)
    assert policies.can_create_community(root)
    assert not policies.can_create_community(AnonymousUser())
    assert not policies.can_manage_categories(creator)
    assert policies.can_manage_categories(functional_admin)
    assert policies.can_manage_categories(root)


def test_membership_is_memoised_per_user_instance(make_community, make_user, add_member):
    community = make_community()
    user = make_user()
    membership = add_member(community, user)
    assert policies.membership_of(user, community) == membership
    with CaptureQueriesContext(connection) as queries:
        assert policies.membership_of(user, community) == membership
        policies.can_manage_members(user, community)
    assert len(queries) == 1  # only the role lookup for the functional-admin check
    other = make_community("Other")
    assert policies.membership_of(user, other) is None
    with CaptureQueriesContext(connection) as queries:
        assert policies.membership_of(user, other) is None
    assert len(queries) == 0
    assert policies.membership_of(user, None) is None
