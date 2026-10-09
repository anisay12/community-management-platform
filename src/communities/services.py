"""Write operations on communities: membership, invitations, roles, lifecycle, categories.

Every service runs in a transaction, takes the actor first, enforces the policies itself
(raising ``DomainError``), records an audit event with the community set and notifies via
``notifications.services.notify`` (rows stored on commit). Membership changes lock the
community row, so concurrent joins serialise and ``member_count`` stays exact.
"""

from collections.abc import Iterable

from django.contrib.postgres.search import SearchVector
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _

from audit.services import record
from core.errors import DomainError
from notifications.services import notify

from . import policies
from .models import (
    REASON_MIN_LENGTH,
    AdminAccessGrant,
    Community,
    CommunityCategory,
    CommunityCreationRequest,
    CommunityInvitation,
    CommunityMembership,
    MembershipRequest,
)
from .roles import CommunityRole, role_at_least

SETTINGS_FIELDS = {
    "name",
    "category",
    "tagline",
    "description",
    "rules",
    "objectives",
    "access_mode",
    "listed",
    "allow_member_uploads",
    "require_post_review",
    "tags",
}
CATEGORY_FIELDS = {"name", "description", "icon", "order", "is_active"}


# --- helpers --------------------------------------------------------------------------------


def _forbidden():
    return DomainError("forbidden", _("You are not allowed to perform this action."))


def _invalid_state():
    return DomainError("invalid_state", _("This action is not possible in the current state."))


def _lock(community: Community) -> Community:
    """Lock the community row and refresh the given instance from it."""
    locked = Community.objects.select_for_update().get(pk=community.pk)
    community.status = locked.status
    community.access_mode = locked.access_mode
    community.member_count = locked.member_count
    community.archived_at = locked.archived_at
    return community


def _ensure_writable(actor, community: Community) -> None:
    if community.status != Community.Status.ACTIVE and not policies.is_functional_admin(actor):
        raise DomainError(
            "read_only", _("This community is suspended or archived: it cannot be changed.")
        )


def _ensure_name_free(name: str, *, exclude_pk=None) -> None:
    taken = Community.objects.filter(name__iexact=name.strip()).exclude(
        status=Community.Status.ARCHIVED
    )
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise DomainError("name_taken", _("A community with this name already exists."))


def _unique_slug(model, name: str, default: str) -> str:
    base = slugify(name)[:120] or default
    slug, suffix = base, 2
    while model.objects.filter(slug=slug).exists():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _refresh_search_vector(community: Community) -> None:
    Community.objects.filter(pk=community.pk).update(
        search_vector=SearchVector("name", weight="A", config="simple")
        + SearchVector("tagline", weight="B", config="simple")
        + SearchVector("description", weight="C", config="simple")
    )


def _add_to_count(community: Community, delta: int) -> None:
    Community.objects.filter(pk=community.pk).update(member_count=F("member_count") + delta)
    community.refresh_from_db(fields=["member_count"])


def _add_membership(community: Community, user, role=CommunityRole.MEMBER) -> CommunityMembership:
    membership = CommunityMembership.objects.create(community=community, user=user, role=role)
    _add_to_count(community, 1)
    policies.clear_membership_cache(user)
    return membership


def _delete_membership(membership: CommunityMembership, *users) -> None:
    """Delete ``membership``; ``users`` are extra instances of its user whose cache to clear."""
    community = membership.community
    membership.delete()
    _add_to_count(community, -1)
    for user in (membership.user, *users):
        policies.clear_membership_cache(user)


def _is_last_owner(membership: CommunityMembership) -> bool:
    if membership.role != CommunityRole.OWNER:
        return False
    return (
        CommunityMembership.objects.filter(
            community_id=membership.community_id, role=CommunityRole.OWNER
        ).count()
        <= 1
    )


def _last_owner_error():
    return DomainError(
        "last_owner",
        _("The community needs at least one lead: appoint another lead first."),
    )


def _managers(community: Community):
    """Members with the facilitator role or above (recipients of membership requests)."""
    return [
        m.user
        for m in community.memberships.select_related("user").filter(
            role__in=[CommunityRole.ANIMATOR, CommunityRole.OWNER]
        )
    ]


def _locked_membership(membership: CommunityMembership) -> CommunityMembership:
    try:
        return CommunityMembership.objects.select_for_update().get(pk=membership.pk)
    except CommunityMembership.DoesNotExist:
        raise DomainError("not_member", _("This person is not a member.")) from None


# --- creation and settings ------------------------------------------------------------------


@transaction.atomic
def create_community(
    *,
    actor,
    name: str,
    category: CommunityCategory,
    tagline: str,
    description: str = "",
    rules: str = "",
    objectives: str = "",
    access_mode: str,
    listed: bool = False,
    tags: Iterable = (),
    owner=None,
) -> Community:
    """Create a community; ``owner`` (default: the actor) becomes its lead.

    ``owner`` is given when a functional admin approves someone else's creation request.
    """
    if not policies.can_create_community(actor):
        raise _forbidden()
    owner = owner or actor
    _ensure_name_free(name)
    community = Community.objects.create(
        name=name.strip(),
        slug=_unique_slug(Community, name, "community"),
        category=category,
        tagline=tagline,
        description=description,
        rules=rules,
        objectives=objectives,
        access_mode=access_mode,
        listed=listed,
        created_by=owner,
    )
    community.tags.set(tags)
    _add_membership(community, owner, CommunityRole.OWNER)
    _refresh_search_vector(community)
    record(
        actor=actor,
        action="community.created",
        target=community,
        changes={"name": community.name, "access_mode": access_mode, "owner": str(owner.pk)},
        community=community,
    )
    return community


def _audit_value(value):
    if hasattr(value, "pk"):
        return value.pk
    return value


@transaction.atomic
def update_settings(*, actor, community: Community, **fields) -> Community:
    """Change the settings of ``community`` (lead or functional admin)."""
    if not policies.can_configure(actor, community):
        raise _forbidden()
    if set(fields) - SETTINGS_FIELDS:
        raise _invalid_state()
    _lock(community)
    _ensure_writable(actor, community)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
        _ensure_name_free(fields["name"], exclude_pk=community.pk)
    changes = {}
    tags = fields.pop("tags", None)
    if tags is not None:
        before = sorted(community.tags.values_list("slug", flat=True))
        community.tags.set(tags)
        after = sorted(community.tags.values_list("slug", flat=True))
        if before != after:
            changes["tags"] = {"before": before, "after": after}
    for field, value in fields.items():
        before = getattr(community, field)
        if before != value:
            changes[field] = {"before": _audit_value(before), "after": _audit_value(value)}
            setattr(community, field, value)
    community.save()
    _refresh_search_vector(community)
    record(
        actor=actor,
        action="community.updated",
        target=community,
        changes=changes,
        community=community,
    )
    return community


# --- joining and requests -------------------------------------------------------------------


@transaction.atomic
def join(*, actor, community: Community, message: str = ""):
    """Join an open community or ask to join an on-request one (idempotent request)."""
    _lock(community)
    _ensure_writable(actor, community)
    if CommunityMembership.objects.filter(community=community, user=actor).exists():
        raise DomainError("already_member", _("You are already a member of this community."))
    if community.access_mode == Community.AccessMode.INVITE:
        raise DomainError("invite_only", _("This community is open by invitation only."))
    if community.access_mode == Community.AccessMode.OPEN:
        membership = _add_membership(community, actor)
        record(actor=actor, action="community.joined", target=community, community=community)
        return membership
    pending = MembershipRequest.objects.filter(
        community=community, user=actor, status=MembershipRequest.Status.PENDING
    ).first()
    if pending is not None:
        return pending
    request = MembershipRequest.objects.create(community=community, user=actor, message=message)
    record(
        actor=actor,
        action="community.membership_requested",
        target=request,
        community=community,
    )
    notify(
        "community.request_received",
        _managers(community),
        actor=actor,
        target=request,
        community=community,
    )
    return request


def _locked_request(request: MembershipRequest) -> MembershipRequest:
    locked = MembershipRequest.objects.select_for_update().get(pk=request.pk)
    if locked.status != MembershipRequest.Status.PENDING:
        raise _invalid_state()
    return locked


@transaction.atomic
def cancel_request(*, actor, request: MembershipRequest) -> MembershipRequest:
    if request.user_id != actor.pk:
        raise _forbidden()
    locked = _locked_request(request)
    locked.status = MembershipRequest.Status.CANCELLED
    locked.save(update_fields=["status", "updated_at"])
    request.status = locked.status
    record(
        actor=actor,
        action="community.request_cancelled",
        target=locked,
        community=request.community,
    )
    return locked


def _decide(actor, request: MembershipRequest, status, note: str) -> MembershipRequest:
    community = request.community
    if not policies.can_manage_members(actor, community):
        raise _forbidden()
    _lock(community)
    _ensure_writable(actor, community)
    locked = _locked_request(request)
    locked.status = status
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.decision_note = note
    locked.save()
    for attr in ("status", "decided_by", "decided_at", "decision_note"):
        setattr(request, attr, getattr(locked, attr))
    return locked


@transaction.atomic
def accept_request(*, actor, request: MembershipRequest) -> CommunityMembership:
    """Accept a pending membership request (facilitator or above, or functional admin)."""
    locked = _decide(actor, request, MembershipRequest.Status.ACCEPTED, "")
    community = request.community
    membership = CommunityMembership.objects.filter(community=community, user=locked.user).first()
    if membership is None:
        membership = _add_membership(community, locked.user)
    record(actor=actor, action="community.request_accepted", target=locked, community=community)
    notify(
        "community.request_accepted",
        [locked.user],
        actor=actor,
        target=community,
        community=community,
    )
    return membership


@transaction.atomic
def reject_request(*, actor, request: MembershipRequest, note: str = "") -> MembershipRequest:
    locked = _decide(actor, request, MembershipRequest.Status.REJECTED, note)
    community = request.community
    record(actor=actor, action="community.request_rejected", target=locked, community=community)
    notify(
        "community.request_rejected",
        [locked.user],
        actor=actor,
        target=community,
        community=community,
    )
    return locked


# --- invitations ----------------------------------------------------------------------------


@transaction.atomic
def invite(*, actor, community: Community, user, role=CommunityRole.MEMBER) -> CommunityInvitation:
    """Invite ``user``; facilitator and above roles may only be offered by a lead or admin."""
    if not policies.can_manage_members(actor, community):
        raise _forbidden()
    if role not in CommunityRole.values or role == CommunityRole.OWNER:
        raise DomainError("forbidden_role", _("You cannot offer this role."))
    if role_at_least(role, CommunityRole.ANIMATOR) and not policies.can_change_roles(
        actor, community
    ):
        raise DomainError("forbidden_role", _("You cannot offer this role."))
    _lock(community)
    _ensure_writable(actor, community)
    if not user.is_active:
        raise _invalid_state()
    if CommunityMembership.objects.filter(community=community, user=user).exists():
        raise DomainError("already_member", _("This person is already a member."))
    pending = CommunityInvitation.objects.filter(
        community=community, invited_user=user, status=CommunityInvitation.Status.PENDING
    ).first()
    if pending is not None and not pending.is_expired:
        return pending
    if pending is not None:
        pending.status = CommunityInvitation.Status.EXPIRED
        pending.save(update_fields=["status", "updated_at"])
    invitation = CommunityInvitation.objects.create(
        community=community, invited_user=user, invited_by=actor, role=role
    )
    record(
        actor=actor,
        action="community.invited",
        target=invitation,
        changes={"user": str(user.pk), "role": str(role)},
        community=community,
    )
    notify("community.invited", [user], actor=actor, target=invitation, community=community)
    return invitation


def _locked_invitation(invitation: CommunityInvitation) -> CommunityInvitation:
    locked = CommunityInvitation.objects.select_for_update().get(pk=invitation.pk)
    if locked.status != CommunityInvitation.Status.PENDING:
        raise _invalid_state()
    return locked


def _respond(actor, invitation: CommunityInvitation) -> CommunityInvitation:
    if invitation.invited_user_id != actor.pk:
        raise _forbidden()
    _lock(invitation.community)
    locked = _locked_invitation(invitation)
    if locked.is_expired:
        raise DomainError("expired", _("This invitation has expired."))
    return locked


@transaction.atomic
def accept_invitation(*, actor, invitation: CommunityInvitation) -> CommunityMembership:
    community = invitation.community
    locked = _respond(actor, invitation)
    _ensure_writable(actor, community)
    locked.status = CommunityInvitation.Status.ACCEPTED
    locked.save(update_fields=["status", "updated_at"])
    invitation.status = locked.status
    membership = CommunityMembership.objects.filter(community=community, user=actor).first()
    if membership is None:
        membership = _add_membership(community, actor, locked.role)
    MembershipRequest.objects.filter(
        community=community, user=actor, status=MembershipRequest.Status.PENDING
    ).update(status=MembershipRequest.Status.CANCELLED, updated_at=timezone.now())
    record(
        actor=actor,
        action="community.invitation_accepted",
        target=locked,
        community=community,
    )
    return membership


@transaction.atomic
def decline_invitation(*, actor, invitation: CommunityInvitation) -> CommunityInvitation:
    locked = _respond(actor, invitation)
    locked.status = CommunityInvitation.Status.DECLINED
    locked.save(update_fields=["status", "updated_at"])
    invitation.status = locked.status
    record(
        actor=actor,
        action="community.invitation_declined",
        target=locked,
        community=invitation.community,
    )
    return locked


@transaction.atomic
def revoke_invitation(*, actor, invitation: CommunityInvitation) -> CommunityInvitation:
    community = invitation.community
    if not policies.can_manage_members(actor, community):
        raise _forbidden()
    locked = _locked_invitation(invitation)
    locked.status = CommunityInvitation.Status.REVOKED
    locked.save(update_fields=["status", "updated_at"])
    invitation.status = locked.status
    record(actor=actor, action="community.invitation_revoked", target=locked, community=community)
    return locked


# --- leaving, removal and roles -------------------------------------------------------------


@transaction.atomic
def leave(*, actor, community: Community) -> None:
    _lock(community)
    _ensure_writable(actor, community)
    membership = (
        CommunityMembership.objects.select_for_update()
        .filter(community=community, user=actor)
        .first()
    )
    if membership is None:
        raise DomainError("not_member", _("You are not a member of this community."))
    if _is_last_owner(membership):
        raise _last_owner_error()
    _delete_membership(membership, actor)
    record(actor=actor, action="community.left", target=community, community=community)


@transaction.atomic
def remove_member(*, actor, membership: CommunityMembership) -> None:
    community = membership.community
    if not policies.can_manage_members(actor, community):
        raise _forbidden()
    _lock(community)
    _ensure_writable(actor, community)
    locked = _locked_membership(membership)
    if role_at_least(locked.role, CommunityRole.ANIMATOR) and not policies.can_change_roles(
        actor, community
    ):
        raise DomainError("forbidden_role", _("You cannot remove a member with this role."))
    if _is_last_owner(locked):
        raise _last_owner_error()
    _delete_membership(locked, membership.user)
    record(
        actor=actor,
        action="community.member_removed",
        target=community,
        changes={"user": str(locked.user_id), "role": locked.role},
        community=community,
    )


@transaction.atomic
def change_role(*, actor, membership: CommunityMembership, role) -> CommunityMembership:
    community = membership.community
    if not policies.can_change_roles(actor, community):
        raise _forbidden()
    if role not in CommunityRole.values:
        raise _invalid_state()
    _lock(community)
    _ensure_writable(actor, community)
    locked = _locked_membership(membership)
    before = locked.role
    if before == role:
        return locked
    if _is_last_owner(locked):
        raise _last_owner_error()
    locked.role = role
    locked.save(update_fields=["role", "updated_at"])
    membership.role = role
    policies.clear_membership_cache(locked.user)
    policies.clear_membership_cache(membership.user)
    record(
        actor=actor,
        action="community.role_changed",
        target=locked.user,
        changes={"role": {"before": str(before), "after": str(role)}},
        community=community,
    )
    return locked


# --- lifecycle ------------------------------------------------------------------------------


def _transition(actor, community, allowed, *, source, target, action, **extra) -> Community:
    if not allowed(actor, community):
        raise _forbidden()
    _lock(community)
    if community.status not in source:
        raise _invalid_state()
    before = community.status
    community.status = target
    for field, value in extra.items():
        setattr(community, field, value)
    community.save(update_fields=["status", "updated_at", *extra])
    record(
        actor=actor,
        action=action,
        target=community,
        changes={"status": {"before": before, "after": target}},
        community=community,
    )
    return community


@transaction.atomic
def suspend(*, actor, community: Community) -> Community:
    return _transition(
        actor,
        community,
        policies.can_suspend,
        source={Community.Status.ACTIVE},
        target=Community.Status.SUSPENDED,
        action="community.suspended",
    )


@transaction.atomic
def reactivate(*, actor, community: Community) -> Community:
    return _transition(
        actor,
        community,
        policies.can_suspend,
        source={Community.Status.SUSPENDED},
        target=Community.Status.ACTIVE,
        action="community.reactivated",
    )


@transaction.atomic
def archive(*, actor, community: Community) -> Community:
    if community.status == Community.Status.SUSPENDED:
        _ensure_writable(actor, community)
    return _transition(
        actor,
        community,
        policies.can_archive,
        source={Community.Status.ACTIVE, Community.Status.SUSPENDED},
        target=Community.Status.ARCHIVED,
        action="community.archived",
        archived_at=timezone.now(),
    )


@transaction.atomic
def unarchive(*, actor, community: Community) -> Community:
    if community.status == Community.Status.ARCHIVED:
        _ensure_name_free(community.name, exclude_pk=community.pk)
    return _transition(
        actor,
        community,
        policies.can_archive,
        source={Community.Status.ARCHIVED},
        target=Community.Status.ACTIVE,
        action="community.unarchived",
        archived_at=None,
    )


# --- creation requests ----------------------------------------------------------------------


@transaction.atomic
def request_creation(
    *, actor, name: str, category: CommunityCategory, justification: str
) -> CommunityCreationRequest:
    if not getattr(actor, "is_active", False):
        raise _forbidden()
    _ensure_name_free(name)
    request = CommunityCreationRequest.objects.create(
        requester=actor, name=name.strip(), category=category, justification=justification
    )
    record(actor=actor, action="community.creation_requested", target=request)
    return request


def _locked_creation_request(actor, request) -> CommunityCreationRequest:
    if not policies.is_functional_admin(actor):
        raise _forbidden()
    locked = CommunityCreationRequest.objects.select_for_update().get(pk=request.pk)
    if locked.status != CommunityCreationRequest.Status.PENDING:
        raise _invalid_state()
    return locked


@transaction.atomic
def approve_creation_request(
    *,
    actor,
    request: CommunityCreationRequest,
    note: str = "",
    tagline: str = "",
    access_mode: str = Community.AccessMode.REQUEST,
) -> Community:
    """Approve a creation request: the community is created with the requester as lead."""
    locked = _locked_creation_request(actor, request)
    community = create_community(
        actor=actor,
        name=locked.name,
        category=locked.category,
        tagline=(tagline or locked.justification)[:160],
        access_mode=access_mode,
        owner=locked.requester,
    )
    locked.status = CommunityCreationRequest.Status.APPROVED
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.decision_note = note
    locked.community = community
    locked.save()
    record(actor=actor, action="community.creation_approved", target=locked, community=community)
    notify(
        "community.creation_decided",
        [locked.requester],
        actor=actor,
        target=locked,
        community=community,
    )
    return community


@transaction.atomic
def reject_creation_request(
    *, actor, request: CommunityCreationRequest, note: str = ""
) -> CommunityCreationRequest:
    locked = _locked_creation_request(actor, request)
    locked.status = CommunityCreationRequest.Status.REJECTED
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.decision_note = note
    locked.save()
    record(
        actor=actor,
        action="community.creation_rejected",
        target=locked,
        changes={"note": note},
    )
    notify("community.creation_decided", [locked.requester], actor=actor, target=locked)
    return locked


# --- administrator access -------------------------------------------------------------------


@transaction.atomic
def grant_admin_access(*, actor, community: Community, reason: str) -> AdminAccessGrant:
    """Give a functional admin one hour of access to the community's content (audited)."""
    if not policies.is_functional_admin(actor):
        raise _forbidden()
    reason = (reason or "").strip()
    if len(reason) < REASON_MIN_LENGTH:
        raise DomainError(
            "invalid_state",
            _("Give a reason of at least %(count)d characters.") % {"count": REASON_MIN_LENGTH},
        )
    grant = AdminAccessGrant.objects.create(community=community, user=actor, reason=reason)
    record(
        actor=actor,
        action="community.admin_access",
        target=community,
        changes={"reason": reason},
        community=community,
    )
    return grant


# --- categories -----------------------------------------------------------------------------


def _ensure_category_admin(actor) -> None:
    if not policies.can_manage_categories(actor):
        raise _forbidden()


def _ensure_category_name_free(name: str, *, exclude_pk=None) -> None:
    taken = CommunityCategory.objects.filter(name__iexact=name.strip())
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    if taken.exists():
        raise DomainError("name_taken", _("A category with this name already exists."))


@transaction.atomic
def create_category(
    *, actor, name: str, description: str = "", icon: str = "people", order: int = 0
) -> CommunityCategory:
    _ensure_category_admin(actor)
    _ensure_category_name_free(name)
    category = CommunityCategory.objects.create(
        name=name.strip(),
        slug=_unique_slug(CommunityCategory, name, "category"),
        description=description,
        icon=icon,
        order=order,
    )
    record(actor=actor, action="category.created", target=category, changes={"name": name})
    return category


@transaction.atomic
def update_category(*, actor, category: CommunityCategory, **fields) -> CommunityCategory:
    _ensure_category_admin(actor)
    if set(fields) - CATEGORY_FIELDS:
        raise _invalid_state()
    if "name" in fields:
        fields["name"] = fields["name"].strip()
        _ensure_category_name_free(fields["name"], exclude_pk=category.pk)
    changes = {}
    for field, value in fields.items():
        before = getattr(category, field)
        if before != value:
            changes[field] = {"before": before, "after": value}
            setattr(category, field, value)
    category.save()
    record(actor=actor, action="category.updated", target=category, changes=changes)
    return category


@transaction.atomic
def deactivate_category(*, actor, category: CommunityCategory) -> CommunityCategory:
    _ensure_category_admin(actor)
    category.is_active = False
    category.save(update_fields=["is_active", "updated_at"])
    record(actor=actor, action="category.deactivated", target=category)
    return category
