from django import template
from django.utils.translation import gettext

from audit import policies

register = template.Library()


@register.filter
def can_view_audit_log(user) -> bool:
    return policies.can_view_audit_log(user)


@register.filter
def action_category(action: str) -> str:
    """Translated category of an audit action."""
    if policies.is_technical_action(action):
        return gettext("Technical and security")
    return gettext("Functional")
