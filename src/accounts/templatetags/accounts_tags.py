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
