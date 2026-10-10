"""``{% moderation_queue_link community %}``: the moderation queue link of a community."""

from django import template
from django.urls import reverse

from posts import policies, selectors_moderation

register = template.Library()


@register.simple_tag(takes_context=True)
def moderation_queue_link(context, community) -> dict | None:
    """``{"url", "count"}`` for moderators+ and functional admins who can read the content
    (any community status), else ``None`` (also on the queue page itself: no link to the
    current page). ``count``: reported targets and pending posts."""
    request = context["request"]
    url = reverse("posts:moderation_queue", args=[community.slug])
    if request.path == url or not policies.can_moderate(request.user, community):
        return None
    reported, pending = selectors_moderation.queue_counts(community)
    return {"url": url, "count": reported + pending}
