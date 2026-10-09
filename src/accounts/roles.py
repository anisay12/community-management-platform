from django.db import models
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    EMPLOYEE = "employee", _("Employee")
    COMMUNITY_CREATOR = "community_creator", _("Community creator")
    FUNCTIONAL_ADMIN = "functional_admin", _("Functional administrator")
    TECHNICAL_ADMIN = "technical_admin", _("Technical administrator")
    AUDITOR = "auditor", _("Auditor")


PRIVILEGED_ROLES = {Role.FUNCTIONAL_ADMIN, Role.TECHNICAL_ADMIN}


# Role codes are memoised on the user instance (one per request); group changes clear it.
ROLES_CACHE_ATTR = "_role_codes"


def user_roles(user) -> set[str]:
    """Return the role codes (group names) held by ``user``."""
    if not getattr(user, "is_authenticated", False):
        return set()
    # Attribute access (not ``__dict__``) so the cache also works through the
    # ``SimpleLazyObject`` wrapping ``request.user``: ``setattr`` writes through to
    # the wrapped instance and ``getattr`` reads through to it.
    cached = getattr(user, ROLES_CACHE_ATTR, None)
    if cached is None:
        codes = {r.value for r in Role}
        cached = frozenset(g.name for g in user.groups.all() if g.name in codes)
        setattr(user, ROLES_CACHE_ATTR, cached)
    return set(cached)


def has_role(user, role) -> bool:
    return str(role) in user_roles(user)


def is_manager(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return user.reports.exists()
