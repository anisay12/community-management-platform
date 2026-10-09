from django_otp import user_has_device

from .backends import is_break_glass
from .roles import PRIVILEGED_ROLES, Role, has_role, user_roles


def requires_mfa(user) -> bool:
    """Whether ``user`` must pass a second factor before using the application."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff or user.is_superuser:
        return True
    if is_break_glass(user):
        return True
    # Last because it needs the user's groups (memoised per user instance).
    # Auditors read every account's e-mail and security events, so they need it too.
    return bool(user_roles(user) & {str(role) for role in PRIVILEGED_ROLES | {Role.AUDITOR}})


def has_confirmed_device(user) -> bool:
    return user_has_device(user, confirmed=True)


def can_manage_users(user) -> bool:
    """Whether ``user`` may administer accounts (MFA is enforced by the middleware)."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    return user.is_superuser or has_role(user, Role.FUNCTIONAL_ADMIN)


def is_protected_account(user) -> bool:
    """Accounts only a superuser may administer: superusers, staff and technical admins."""
    return bool(user.is_superuser or user.is_staff or has_role(user, Role.TECHNICAL_ADMIN))


def hierarchy_allows(actor, target) -> bool:
    """The account hierarchy rule: only a superuser may act on a protected account."""
    return bool(getattr(actor, "is_superuser", False)) or not is_protected_account(target)


def can_administer_user(actor, target) -> bool:
    """Whether ``actor`` may change the status or the roles of ``target``."""
    return can_manage_users(actor) and hierarchy_allows(actor, target)


def shares_community(viewer, owner) -> bool:
    """Whether ``viewer`` and ``owner`` are members of a common community.

    Communities arrive in lot L3, which replaces this placeholder; until then no two
    users share a community, so ``communities`` visibility behaves like ``private`` for
    everyone but the owner and administrators.
    """
    return False


def can_view_profile(viewer, owner) -> bool:
    """Whether ``viewer`` may see ``owner``'s profile page at all (otherwise 404).

    Only active accounts are visible to everyone; a pending, suspended or deactivated
    owner is visible to themself and to user administrators only.
    """
    if not getattr(viewer, "is_authenticated", False) or not viewer.is_active:
        return False
    return viewer.pk == owner.pk or owner.status == owner.Status.ACTIVE or can_manage_users(viewer)


def can_view_profile_details(viewer, owner) -> bool:
    """Whether ``viewer`` may see ``owner``'s bio and interests."""
    if not can_view_profile(viewer, owner):
        return False
    if viewer.pk == owner.pk or can_manage_users(viewer):
        return True
    visibility = owner.profile.profile_visibility
    if visibility == owner.profile.Visibility.COMPANY:
        return True
    if visibility == owner.profile.Visibility.COMMUNITIES:
        return shares_community(viewer, owner)
    return False
