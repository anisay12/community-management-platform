"""``{% moderation_queue_link community %}``: the moderation queue link of a community."""

from django import template
from django.urls import reverse

from posts import policies, selectors_moderation

register = template.Library()


@register.simple_tag(takes_context=True)
def moderation_queue_link(context, community) -> dict | None:
    """``{"url", "count"}`` for moderators+ and functional admins who can read the content
    (any community status), else ``None``. ``count``: reported targets and pending posts."""
    if not policies.can_moderate(context["request"].user, community):
        return None
    reported, pending = selectors_moderation.queue_counts(community)
    url = reverse("posts:moderation_queue", args=[community.slug])
    return {"url": url, "count": reported + pending}
