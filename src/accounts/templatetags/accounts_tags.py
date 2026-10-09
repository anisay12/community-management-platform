from django import template

from accounts import policies
from accounts.roles import Role

register = template.Library()


@register.filter
def can_manage_users(user) -> bool:
    return policies.can_manage_users(user)


@register.filter
def role_labels(user) -> list[str]:
    """Translated role labels of ``user``, read from its (prefetched) groups."""
    names = {group.name for group in user.groups.all()}
    return [role.label for role in Role if role.value in names]


@register.filter
def initials(user) -> str:
    """Up to two initials for the avatar placeholder (no photo upload before L5)."""
    words = user.get_full_name().split() or [user.email]
    letters = [words[0][0], words[-1][0]] if len(words) > 1 else [words[0][0]]
    return "".join(letters).upper()
