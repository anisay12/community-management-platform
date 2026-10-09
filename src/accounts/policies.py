from django.conf import settings
from django_otp import user_has_device

from .roles import PRIVILEGED_ROLES, Role, has_role, user_roles


def requires_mfa(user) -> bool:
    """Whether ``user`` must pass a second factor before using the application."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff or user.is_superuser:
        return True
    break_glass = settings.BREAK_GLASS_EMAIL.strip().lower()
    if break_glass and user.email.lower() == break_glass:
        return True
    # Last because it needs the user's groups (memoised per user instance).
    return bool(user_roles(user) & {str(role) for role in PRIVILEGED_ROLES})


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
