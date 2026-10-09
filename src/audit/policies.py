from accounts.roles import Role, has_role

# Actions recorded for authentication and security; every other action is functional.
TECHNICAL_ACTION_PREFIXES = ("auth.",)

FUNCTIONAL = "functional"
TECHNICAL = "technical"


def is_technical_action(action: str) -> bool:
    """Whether ``action`` is a technical or security event (as opposed to a functional one)."""
    return action.startswith(TECHNICAL_ACTION_PREFIXES)


def audit_scopes(user) -> frozenset[str]:
    """The audit event scopes ``user`` may see, among ``"functional"`` and ``"technical"``.

    Empty for anonymous and inactive users. Superusers and auditors see both scopes;
    administrators see the scope of their role, and the union when they hold several.
    """
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return frozenset()
    if user.is_superuser or has_role(user, Role.AUDITOR):
        return frozenset({FUNCTIONAL, TECHNICAL})
    scopes = set()
    if has_role(user, Role.TECHNICAL_ADMIN):
        scopes.add(TECHNICAL)
    if has_role(user, Role.FUNCTIONAL_ADMIN):
        scopes.add(FUNCTIONAL)
    return frozenset(scopes)


def can_view_audit_log(user) -> bool:
    """Whether ``user`` may see the audit log at all (otherwise 404)."""
    return bool(audit_scopes(user))
