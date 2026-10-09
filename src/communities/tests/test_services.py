from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.contrib.postgres.search import SearchQuery
from django.template.loader import render_to_string
from django.utils import timezone, translation

from accounts.roles import Role
from audit.models import AuditEvent
from communities import policies, services
from communities.models import (
    AdminAccessGrant,
    Community,
    CommunityCategory,
    CommunityCreationRequest,
    CommunityInvitation,
    CommunityMembership,
    MembershipRequest,
)
from communities.roles import CommunityRole
from core.errors import DomainError
from notifications.models import Notification
from taxonomy.models import Tag

pytestmark = pytest.mark.django_db


@pytest.fixture
def creator(make_user):
    user = make_user("creator@example.com", first_name="Cora")
    user.groups.add(Group.objects.get(name=Role.COMMUNITY_CREATOR))
    return user


@pytest.fixture
def bob(make_user):
    return make_user("bob@example.com", first_name="Bob")


@pytest.fixture
def carol(make_user):
    return make_user("carol@example.com", first_name="Carol")


@pytest.fixture
def new_community(creator, category):
    def _new(name="Python guild", access_mode=Community.AccessMode.OPEN, **extra):
        extra.setdefault("tagline", "Everything Python")
        return services.create_community(
            actor=creator, name=name, category=category, access_mode=access_mode, **extra
        )

    return _new


def _error(excinfo):
    return excinfo.value.code


def _events(action):
    return AuditEvent.objects.filter(action=action)


def _membership(community, user):
    return CommunityMembership.objects.get(community=community, user=user)


# --- creation and settings -------------------------------------------------------------------


def test_create_community_makes_creator_owner(new_community, creator):
    tag = Tag.objects.create(name="Django", slug="django")
    community = new_community(tags=[tag], listed=True)
    assert community.slug == "python-guild"
    assert community.member_count == 1
    assert community.created_by == creator
    assert list(community.tags.all()) == [tag]
    assert _membership(community, creator).role == CommunityRole.OWNER
    event = _events("community.created").get()
    assert event.community_id == community.pk
    assert event.actor == creator


def test_create_community_refreshes_search_vector(new_community):
    community = new_community(description="Snakes and asyncio")
    assert Community.objects.filter(
        pk=community.pk, search_vector=SearchQuery("asyncio", config="simple")
    ).exists()


def test_create_community_requires_permission(bob, category):
    with pytest.raises(DomainError) as excinfo:
        services.create_community(
            actor=bob, name="X", category=category, tagline="t", access_mode="open"
        )
    assert _error(excinfo) == "forbidden"


def test_create_community_name_taken_case_insensitive(new_community):
    first = new_community()
    with pytest.raises(DomainError) as excinfo:
        new_community(name="PYTHON guild")
    assert _error(excinfo) == "name_taken"
    # An archived community frees its name; the slug gets a suffix.
    Community.objects.filter(pk=first.pk).update(status=Community.Status.ARCHIVED)
    assert new_community(name="python Guild").slug == "python-guild-2"


def test_update_settings_records_changes_and_search_vector(new_community, creator):
    community = new_community()
    services.update_settings(
        actor=creator, community=community, tagline="Typed Python", description="mypy"
    )
    community.refresh_from_db()
    assert community.tagline == "Typed Python"
    event = _events("community.updated").get()
    assert event.changes["tagline"] == {"before": "Everything Python", "after": "Typed Python"}
    assert event.community_id == community.pk
    assert Community.objects.filter(
        pk=community.pk, search_vector=SearchQuery("mypy", config="simple")
    ).exists()


def test_update_settings_permissions_and_rules(new_community, bob, functional_admin, creator):
    community = new_community()
    with pytest.raises(DomainError) as excinfo:
        services.update_settings(actor=bob, community=community, tagline="x")
    assert _error(excinfo) == "forbidden"
    with pytest.raises(DomainError) as excinfo:
        services.update_settings(actor=creator, community=community, status="archived")
    assert _error(excinfo) == "invalid_state"
    new_community(name="Other")
    with pytest.raises(DomainError) as excinfo:
        services.update_settings(actor=creator, community=community, name="other")
    assert _error(excinfo) == "name_taken"
    services.suspend(actor=functional_admin, community=community)
    with pytest.raises(DomainError) as excinfo:
        services.update_settings(actor=creator, community=community, tagline="x")
    assert _error(excinfo) == "read_only"
    services.update_settings(actor=functional_admin, community=community, tagline="ok")
    tag = Tag.objects.create(name="Web", slug="web")
    services.update_settings(actor=functional_admin, community=community, tags=[tag])
    assert list(community.tags.all()) == [tag]


# --- joining ---------------------------------------------------------------------------------


def test_join_open_community(new_community, bob):
    community = new_community()
    membership = services.join(actor=bob, community=community)
    assert isinstance(membership, CommunityMembership)
    assert membership.role == CommunityRole.MEMBER
    community.refresh_from_db()
    assert community.member_count == 2
    assert _events("community.joined").get().community_id == community.pk
    with pytest.raises(DomainError) as excinfo:
        services.join(actor=bob, community=community)
    assert _error(excinfo) == "already_member"


def test_policy_cache_is_cleared_after_join_and_leave(new_community, bob):
    community = new_community()
    assert policies.membership_of(bob, community) is None
    services.join(actor=bob, community=community)
    assert policies.membership_of(bob, community) is not None
    services.leave(actor=bob, community=community)
    assert policies.membership_of(bob, community) is None
    assert policies.can_join(bob, community)


def test_join_request_community_is_idempotent_and_notifies(
    new_community, bob, creator, django_capture_on_commit_callbacks
):
    community = new_community(access_mode=Community.AccessMode.REQUEST)
    with django_capture_on_commit_callbacks(execute=True):
        first = services.join(actor=bob, community=community, message="Hello")
        second = services.join(actor=bob, community=community)
    assert isinstance(first, MembershipRequest)
    assert first.pk == second.pk
    assert first.message == "Hello"
    assert MembershipRequest.objects.count() == 1
    assert _events("community.membership_requested").count() == 1
    notification = Notification.objects.get(category="community.request_received")
    assert notification.recipient == creator
    community.refresh_from_db()
    assert community.member_count == 1


def test_join_invite_only_community(new_community, bob):
    community = new_community(access_mode=Community.AccessMode.INVITE)
    with pytest.raises(DomainError) as excinfo:
        services.join(actor=bob, community=community)
    assert _error(excinfo) == "invite_only"


def test_join_suspended_community_is_read_only(new_community, bob, functional_admin):
    community = new_community()
    services.suspend(actor=functional_admin, community=community)
    with pytest.raises(DomainError) as excinfo:
        services.join(actor=bob, community=community)
    assert _error(excinfo) == "read_only"


def test_cancel_request(new_community, bob, carol):
    community = new_community(access_mode=Community.AccessMode.REQUEST)
    request = services.join(actor=bob, community=community)
    with pytest.raises(DomainError) as excinfo:
        services.cancel_request(actor=carol, request=request)
    assert _error(excinfo) == "forbidden"
    services.cancel_request(actor=bob, request=request)
    request.refresh_from_db()
    assert request.status == MembershipRequest.Status.CANCELLED
    with pytest.raises(DomainError) as excinfo:
        services.cancel_request(actor=bob, request=request)
    assert _error(excinfo) == "invalid_state"
    # A new request is possible once the previous one is cancelled.
    assert services.join(actor=bob, community=community).pk != request.pk


def test_accept_request_by_animator(
    new_community, creator, bob, carol, django_capture_on_commit_callbacks
):
    community = new_community(access_mode=Community.AccessMode.REQUEST)
    services.accept_request(actor=creator, request=services.join(actor=carol, community=community))
    services.change_role(
        actor=creator, membership=_membership(community, carol), role=CommunityRole.ANIMATOR
    )
    request = services.join(actor=bob, community=community)
    with django_capture_on_commit_callbacks(execute=True):
        membership = services.accept_request(actor=carol, request=request)
    request.refresh_from_db()
    assert request.status == MembershipRequest.Status.ACCEPTED
    assert request.decided_by == carol
    assert membership.user == bob
    community.refresh_from_db()
    assert community.member_count == 3
    assert _events("community.request_accepted").filter(community=community).count() == 2
    assert Notification.objects.filter(
        category="community.request_accepted", recipient=bob
    ).exists()
    with pytest.raises(DomainError) as excinfo:
        services.accept_request(actor=carol, request=request)
    assert _error(excinfo) == "invalid_state"


def test_accept_and_reject_require_animator(new_community, creator, bob, carol, add_member):
    community = new_community(access_mode=Community.AccessMode.REQUEST)
    add_member(community, carol, role=CommunityRole.MODERATOR)
    request = services.join(actor=bob, community=community)
    for decide in (services.accept_request, services.reject_request):
        with pytest.raises(DomainError) as excinfo:
            decide(actor=carol, request=request)
        assert _error(excinfo) == "forbidden"


def test_reject_request(new_community, creator, bob, django_capture_on_commit_callbacks):
    community = new_community(access_mode=Community.AccessMode.REQUEST)
    request = services.join(actor=bob, community=community)
    with django_capture_on_commit_callbacks(execute=True):
        services.reject_request(actor=creator, request=request, note="Not now")
    request.refresh_from_db()
    assert request.status == MembershipRequest.Status.REJECTED
    assert request.decision_note == "Not now"
    assert not CommunityMembership.objects.filter(community=community, user=bob).exists()
    assert _events("community.request_rejected").get().community_id == community.pk
    assert Notification.objects.filter(category="community.request_rejected").count() == 1


# --- invitations -----------------------------------------------------------------------------


def test_invite_and_accept(new_community, creator, bob, django_capture_on_commit_callbacks):
    community = new_community(access_mode=Community.AccessMode.INVITE)
    with django_capture_on_commit_callbacks(execute=True):
        invitation = services.invite(
            actor=creator, community=community, user=bob, role=CommunityRole.EXPERT
        )
    assert invitation.expires_at > timezone.now() + timedelta(days=13)
    assert Notification.objects.filter(category="community.invited", recipient=bob).exists()
    assert _events("community.invited").get().community_id == community.pk
    # Inviting again returns the pending invitation.
    assert services.invite(actor=creator, community=community, user=bob).pk == invitation.pk
    assert policies.membership_of(bob, community) is None
    membership = services.accept_invitation(actor=bob, invitation=invitation)
    assert membership.role == CommunityRole.EXPERT
    assert policies.membership_of(bob, community) is not None
    invitation.refresh_from_db()
    assert invitation.status == CommunityInvitation.Status.ACCEPTED
    community.refresh_from_db()
    assert community.member_count == 2
    assert _events("community.invitation_accepted").count() == 1
    with pytest.raises(DomainError) as excinfo:
        services.invite(actor=creator, community=community, user=bob)
    assert _error(excinfo) == "already_member"


def test_invitation_roles(new_community, creator, bob, carol, add_member, functional_admin):
    community = new_community()
    add_member(community, carol, role=CommunityRole.ANIMATOR)
    with pytest.raises(DomainError) as excinfo:
        services.invite(actor=carol, community=community, user=bob, role=CommunityRole.ANIMATOR)
    assert _error(excinfo) == "forbidden_role"
    with pytest.raises(DomainError) as excinfo:
        services.invite(actor=creator, community=community, user=bob, role=CommunityRole.OWNER)
    assert _error(excinfo) == "forbidden_role"
    services.invite(actor=carol, community=community, user=bob, role=CommunityRole.MODERATOR)
    other = new_community(name="Other")
    assert services.invite(
        actor=functional_admin, community=other, user=bob, role=CommunityRole.ANIMATOR
    )
    assert services.invite(actor=creator, community=other, user=carol, role="animator")


def test_invite_requires_manager(new_community, bob, carol):
    community = new_community()
    services.join(actor=carol, community=community)
    with pytest.raises(DomainError) as excinfo:
        services.invite(actor=carol, community=community, user=bob)
    assert _error(excinfo) == "forbidden"


def test_expired_invitation(new_community, creator, bob):
    community = new_community(access_mode=Community.AccessMode.INVITE)
    invitation = services.invite(actor=creator, community=community, user=bob)
    CommunityInvitation.objects.filter(pk=invitation.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    invitation.refresh_from_db()
    with pytest.raises(DomainError) as excinfo:
        services.accept_invitation(actor=bob, invitation=invitation)
    assert _error(excinfo) == "expired"
    assert not CommunityMembership.objects.filter(community=community, user=bob).exists()


def test_invitation_respond_only_by_invitee(new_community, creator, bob, carol):
    community = new_community(access_mode=Community.AccessMode.INVITE)
    invitation = services.invite(actor=creator, community=community, user=bob)
    for respond in (services.accept_invitation, services.decline_invitation):
        with pytest.raises(DomainError) as excinfo:
            respond(actor=carol, invitation=invitation)
        assert _error(excinfo) == "forbidden"
    services.decline_invitation(actor=bob, invitation=invitation)
    invitation.refresh_from_db()
    assert invitation.status == CommunityInvitation.Status.DECLINED
    assert _events("community.invitation_declined").count() == 1
    with pytest.raises(DomainError) as excinfo:
        services.accept_invitation(actor=bob, invitation=invitation)
    assert _error(excinfo) == "invalid_state"


def test_revoke_invitation(new_community, creator, bob, carol):
    community = new_community(access_mode=Community.AccessMode.INVITE)
    invitation = services.invite(actor=creator, community=community, user=bob)
    with pytest.raises(DomainError) as excinfo:
        services.revoke_invitation(actor=carol, invitation=invitation)
    assert _error(excinfo) == "forbidden"
    services.revoke_invitation(actor=creator, invitation=invitation)
    invitation.refresh_from_db()
    assert invitation.status == CommunityInvitation.Status.REVOKED
    assert _events("community.invitation_revoked").get().community_id == community.pk
    with pytest.raises(DomainError) as excinfo:
        services.revoke_invitation(actor=creator, invitation=invitation)
    assert _error(excinfo) == "invalid_state"


# --- leaving, removal and roles -------------------------------------------------------------


def test_last_owner_cannot_leave(new_community, creator):
    community = new_community()
    with pytest.raises(DomainError) as excinfo:
        services.leave(actor=creator, community=community)
    assert _error(excinfo) == "last_owner"


def test_leave_removes_only_the_membership(new_community, creator, bob):
    community = new_community()
    services.join(actor=bob, community=community)
    services.leave(actor=bob, community=community)
    assert not CommunityMembership.objects.filter(community=community, user=bob).exists()
    assert CommunityMembership.objects.filter(community=community, user=creator).exists()
    community.refresh_from_db()
    assert community.member_count == 1
    assert _events("community.left").get().community_id == community.pk
    with pytest.raises(DomainError) as excinfo:
        services.leave(actor=bob, community=community)
    assert _error(excinfo) == "not_member"


def test_owner_can_leave_when_another_owner_exists(new_community, creator, bob):
    community = new_community()
    services.join(actor=bob, community=community)
    services.change_role(
        actor=creator, membership=_membership(community, bob), role=CommunityRole.OWNER
    )
    services.leave(actor=creator, community=community)
    community.refresh_from_db()
    assert community.member_count == 1


def test_change_role(new_community, creator, bob):
    community = new_community()
    services.join(actor=bob, community=community)
    membership = _membership(community, bob)
    services.change_role(actor=creator, membership=membership, role=CommunityRole.MODERATOR)
    membership.refresh_from_db()
    assert membership.role == CommunityRole.MODERATOR
    event = _events("community.role_changed").get()
    assert event.community_id == community.pk
    assert event.changes == {"role": {"before": "member", "after": "moderator"}}
    with pytest.raises(DomainError) as excinfo:
        services.change_role(actor=creator, membership=membership, role="nonsense")
    assert _error(excinfo) == "invalid_state"


def test_last_owner_cannot_be_demoted(new_community, creator, functional_admin):
    community = new_community()
    with pytest.raises(DomainError) as excinfo:
        services.change_role(
            actor=functional_admin,
            membership=_membership(community, creator),
            role=CommunityRole.MEMBER,
        )
    assert _error(excinfo) == "last_owner"


def test_change_role_requires_owner(new_community, bob, carol, add_member):
    community = new_community()
    add_member(community, carol, role=CommunityRole.ANIMATOR)
    member = add_member(community, bob)
    with pytest.raises(DomainError) as excinfo:
        services.change_role(actor=carol, membership=member, role=CommunityRole.EXPERT)
    assert _error(excinfo) == "forbidden"


def test_remove_member(new_community, creator, bob, carol, add_member):
    community = new_community()
    services.join(actor=bob, community=community)
    add_member(community, carol, role=CommunityRole.ANIMATOR)
    owner_membership = _membership(community, creator)
    with pytest.raises(DomainError) as excinfo:
        services.remove_member(actor=carol, membership=owner_membership)
    assert _error(excinfo) == "forbidden_role"
    with pytest.raises(DomainError) as excinfo:
        services.remove_member(actor=bob, membership=_membership(community, carol))
    assert _error(excinfo) == "forbidden"
    with pytest.raises(DomainError) as excinfo:
        services.remove_member(actor=creator, membership=owner_membership)
    assert _error(excinfo) == "last_owner"
    services.remove_member(actor=carol, membership=_membership(community, bob))
    assert not CommunityMembership.objects.filter(community=community, user=bob).exists()
    community.refresh_from_db()
    assert community.member_count == 1  # carol was added without the service
    assert _events("community.member_removed").get().community_id == community.pk


# --- lifecycle -------------------------------------------------------------------------------


def test_suspend_and_reactivate(new_community, creator, functional_admin, bob):
    community = new_community()
    with pytest.raises(DomainError) as excinfo:
        services.suspend(actor=creator, community=community)
    assert _error(excinfo) == "forbidden"
    services.suspend(actor=functional_admin, community=community)
    assert community.status == Community.Status.SUSPENDED
    with pytest.raises(DomainError) as excinfo:
        services.suspend(actor=functional_admin, community=community)
    assert _error(excinfo) == "invalid_state"
    # Functional admins may still change memberships of a suspended community.
    services.invite(actor=functional_admin, community=community, user=bob)
    with pytest.raises(DomainError) as excinfo:
        services.leave(actor=creator, community=community)
    assert _error(excinfo) == "read_only"
    services.reactivate(actor=functional_admin, community=community)
    community.refresh_from_db()
    assert community.status == Community.Status.ACTIVE
    actions = set(_events("community.suspended").values_list("community_id", flat=True))
    assert actions == {community.pk}
    assert _events("community.reactivated").count() == 1
    with pytest.raises(DomainError) as excinfo:
        services.reactivate(actor=functional_admin, community=community)
    assert _error(excinfo) == "invalid_state"


def test_archive_and_unarchive(new_community, creator, bob, functional_admin):
    community = new_community()
    with pytest.raises(DomainError) as excinfo:
        services.archive(actor=bob, community=community)
    assert _error(excinfo) == "forbidden"
    services.archive(actor=creator, community=community)
    community.refresh_from_db()
    assert community.status == Community.Status.ARCHIVED
    assert community.archived_at is not None
    with pytest.raises(DomainError) as excinfo:
        services.join(actor=bob, community=community)
    assert _error(excinfo) == "read_only"
    with pytest.raises(DomainError) as excinfo:
        services.archive(actor=creator, community=community)
    assert _error(excinfo) == "invalid_state"
    new_community(name="Python Guild")
    with pytest.raises(DomainError) as excinfo:
        services.unarchive(actor=functional_admin, community=community)
    assert _error(excinfo) == "name_taken"
    Community.objects.filter(name="Python Guild").update(name="Renamed")
    services.unarchive(actor=functional_admin, community=community)
    community.refresh_from_db()
    assert community.status == Community.Status.ACTIVE
    assert community.archived_at is None
    assert _events("community.archived").count() == 1
    assert _events("community.unarchived").count() == 1
    with pytest.raises(DomainError) as excinfo:
        services.unarchive(actor=functional_admin, community=community)
    assert _error(excinfo) == "invalid_state"


# --- creation requests -----------------------------------------------------------------------


def test_creation_request_approval(
    bob, category, functional_admin, django_capture_on_commit_callbacks
):
    request = services.request_creation(
        actor=bob, name="Rust club", category=category, justification="Many Rust fans here."
    )
    assert request.status == CommunityCreationRequest.Status.PENDING
    assert _events("community.creation_requested").count() == 1
    with pytest.raises(DomainError) as excinfo:
        services.approve_creation_request(actor=bob, request=request)
    assert _error(excinfo) == "forbidden"
    with django_capture_on_commit_callbacks(execute=True):
        community = services.approve_creation_request(actor=functional_admin, request=request)
    request.refresh_from_db()
    assert request.status == CommunityCreationRequest.Status.APPROVED
    assert request.community == community
    assert request.decided_by == functional_admin
    assert community.name == "Rust club"
    assert community.created_by == bob
    assert _membership(community, bob).role == CommunityRole.OWNER
    assert not CommunityMembership.objects.filter(user=functional_admin).exists()
    assert Notification.objects.filter(
        category="community.creation_decided", recipient=bob
    ).exists()
    assert _events("community.creation_approved").get().community_id == community.pk
    with pytest.raises(DomainError) as excinfo:
        services.reject_creation_request(actor=functional_admin, request=request)
    assert _error(excinfo) == "invalid_state"


def test_creation_request_rejection(bob, category, functional_admin):
    request = services.request_creation(
        actor=bob, name="Rust club", category=category, justification="Many Rust fans here."
    )
    services.reject_creation_request(actor=functional_admin, request=request, note="Duplicate")
    request.refresh_from_db()
    assert request.status == CommunityCreationRequest.Status.REJECTED
    assert request.decision_note == "Duplicate"
    assert not Community.objects.exists()
    assert _events("community.creation_rejected").count() == 1


def test_creation_request_name_taken(new_community, bob, category):
    new_community()
    with pytest.raises(DomainError) as excinfo:
        services.request_creation(
            actor=bob, name="python guild", category=category, justification="Again"
        )
    assert _error(excinfo) == "name_taken"


# --- administrator access --------------------------------------------------------------------


def test_grant_admin_access(new_community, functional_admin, bob):
    community = new_community(access_mode=Community.AccessMode.INVITE)
    with pytest.raises(DomainError) as excinfo:
        services.grant_admin_access(actor=bob, community=community, reason="Investigating abuse")
    assert _error(excinfo) == "forbidden"
    with pytest.raises(DomainError) as excinfo:
        services.grant_admin_access(actor=functional_admin, community=community, reason="  short ")
    assert _error(excinfo) == "invalid_state"
    assert not policies.can_view_content(functional_admin, community)
    grant = services.grant_admin_access(
        actor=functional_admin, community=community, reason="Investigating abuse"
    )
    assert timedelta(minutes=59) < grant.expires_at - timezone.now() <= timedelta(hours=1)
    assert policies.can_view_content(functional_admin, community)
    event = _events("community.admin_access").get()
    assert event.community_id == community.pk
    assert event.changes == {"reason": "Investigating abuse"}
    AdminAccessGrant.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert not policies.can_view_content(functional_admin, community)


# --- categories ------------------------------------------------------------------------------


def test_category_services(functional_admin, bob):
    with pytest.raises(DomainError) as excinfo:
        services.create_category(actor=bob, name="Quantum")
    assert _error(excinfo) == "forbidden"
    category = services.create_category(actor=functional_admin, name="Quantum", icon="atom")
    assert category.slug == "quantum"
    with pytest.raises(DomainError) as excinfo:
        services.create_category(actor=functional_admin, name="quantum")
    assert _error(excinfo) == "name_taken"
    services.update_category(actor=functional_admin, category=category, description="Qubits")
    category.refresh_from_db()
    assert category.description == "Qubits"
    services.deactivate_category(actor=functional_admin, category=category)
    category.refresh_from_db()
    assert not category.is_active
    actions = list(
        AuditEvent.objects.filter(action__startswith="category.").values_list("action", flat=True)
    )
    assert sorted(actions) == ["category.created", "category.deactivated", "category.updated"]
    with pytest.raises(DomainError) as excinfo:
        services.update_category(actor=functional_admin, category=category, slug="x")
    assert _error(excinfo) == "invalid_state"
    other = CommunityCategory.objects.exclude(pk=category.pk).first()
    with pytest.raises(DomainError) as excinfo:
        services.update_category(actor=functional_admin, category=category, name=other.name)
    assert _error(excinfo) == "name_taken"


# --- component ------------------------------------------------------------------------------


def test_community_role_badge_renders_translated_label():
    with translation.override("en"):
        html = render_to_string("components/community_role_badge.html", {"role": "animator"})
    assert "Facilitator" in html
    assert "badge" in html
