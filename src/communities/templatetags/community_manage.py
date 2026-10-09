"""``{% community_manage_url community %}``: entry point of the management pages, or ""."""

from django import template
from django.urls import reverse

from communities import policies

register = template.Library()


@register.simple_tag(takes_context=True)
def community_manage_url(context, community) -> str:
    user = context["request"].user
    if policies.can_configure(user, community):
        return reverse("communities:manage_settings", args=[community.slug])
    if policies.can_manage_members(user, community):
        return reverse("communities:manage_members", args=[community.slug])
    return ""
