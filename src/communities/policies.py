"""Authorization rules for communities: pure predicates that never raise.

Visibility: a community the user may not see (``can_view_metadata`` false) answers 404;
a visible community where the action is refused answers 403.
"""

from django.utils import timezone

from accounts.roles import Role, has_role

from .models import AdminAccessGrant, Community, CommunityMembership
from .roles import CommunityRole, role_at_least

# Memberships are memoised on the user instance (one per request), keyed by community pk,
# like ``accounts.roles.user_roles``. Services that change memberships clear it.
MEMBERSHIP_CACHE_ATTR = "_community_memberships"
_MISSING = object()


def _is_active_user(user) -> bool:
    return bool(getattr(user, "is_authenticated", False) and user.is_active)


def is_functional_admin(user) -> bool:
    """Functional administrators (or superusers) with an active account."""
    if not _is_active_user(user):
        return False
    return user.is_superuser or has_role(user, Role.FUNCTIONAL_ADMIN)


def clear_membership_cache(user) -> None:
    if getattr(user, MEMBERSHIP_CACHE_ATTR, None) is not None:
        setattr(user, MEMBERSHIP_CACHE_ATTR, None)


def membership_of(user, community) -> CommunityMembership | None:
    """The membership of ``user`` in ``community``, or ``None`` (memoised per user instance)."""
    if not _is_active_user(user) or community is None or community.pk is None:
        return None
    cache = getattr(user, MEMBERSHIP_CACHE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(user, MEMBERSHIP_CACHE_ATTR, cache)
    found = cache.get(community.pk, _MISSING)
    if found is _MISSING:
        found = CommunityMembership.objects.filter(user=user, community=community).first()
        cache[community.pk] = found
    return found


def _has_role(user, community, minimum) -> bool:
    membership = membership_of(user, community)
    return membership is not None and role_at_least(membership.role, minimum)


def has_valid_admin_grant(user, community) -> bool:
    return AdminAccessGrant.objects.filter(
        community=community, user=user, expires_at__gt=timezone.now()
    ).exists()


def can_view_metadata(user, community) -> bool:
    """Whether ``user`` may see the community's name, description and settings."""
    if not _is_active_user(user):
        return False
    if is_functional_admin(user) or membership_of(user, community) is not None:
        return True
    if community.status == Community.Status.ARCHIVED:
        return False
    if community.access_mode == Community.AccessMode.INVITE:
        return community.listed
    return True


def can_view_content(user, community) -> bool:
    """Whether ``user`` may read the community's content (feed, members, resources)."""
    if not _is_active_user(user):
        return False
    if membership_of(user, community) is not None:
        return True
    open_and_live = (
        community.access_mode == Community.AccessMode.OPEN
        and community.status != Community.Status.ARCHIVED
    )
    if is_functional_admin(user):
        # Framing 4.4: open content like everyone; other content only through a stated reason.
        return open_and_live or has_valid_admin_grant(user, community)
    if has_role(user, Role.TECHNICAL_ADMIN) or has_role(user, Role.AUDITOR):
        # Framing 4.4: the technical admin has no content access, the auditor metadata only.
        return False
    return open_and_live


def can_join(user, community) -> bool:
    """Whether ``user`` may join (open) or ask to join (on request) ``community``."""
    if not _is_active_user(user) or membership_of(user, community) is not None:
        return False
    return (
        community.status == Community.Status.ACTIVE
        and community.access_mode != Community.AccessMode.INVITE
    )


def can_manage_members(user, community) -> bool:
    return is_functional_admin(user) or _has_role(user, community, CommunityRole.ANIMATOR)


def can_change_roles(user, community) -> bool:
    return is_functional_admin(user) or _has_role(user, community, CommunityRole.OWNER)


def can_configure(user, community) -> bool:
    return is_functional_admin(user) or _has_role(user, community, CommunityRole.OWNER)


def can_suspend(user, community) -> bool:
    return is_functional_admin(user)


def can_archive(user, community) -> bool:
    return is_functional_admin(user) or _has_role(user, community, CommunityRole.OWNER)


def can_create_community(user) -> bool:
    if not _is_active_user(user):
        return False
    return is_functional_admin(user) or has_role(user, Role.COMMUNITY_CREATOR)


def can_manage_categories(user) -> bool:
    return is_functional_admin(user)
