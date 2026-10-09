"""Community roles (distinct from the platform roles in ``accounts.roles``)."""

from django.db import models
from django.utils.translation import gettext_lazy as _


class CommunityRole(models.TextChoices):
    MEMBER = "member", _("Member")
    CONTRIBUTOR = "contributor", _("Contributor")
    EXPERT = "expert", _("Expert")
    MODERATOR = "moderator", _("Moderator")
    ANIMATOR = "animator", _("Facilitator")
    OWNER = "owner", _("Lead")


ROLE_RANK = {
    CommunityRole.MEMBER: 1,
    CommunityRole.CONTRIBUTOR: 2,
    CommunityRole.EXPERT: 3,
    CommunityRole.MODERATOR: 4,
    CommunityRole.ANIMATOR: 5,
    CommunityRole.OWNER: 6,
}


def role_at_least(role, minimum) -> bool:
    """Whether ``role`` ranks at or above ``minimum`` (unknown roles rank below everything)."""
    return ROLE_RANK.get(str(role), 0) >= ROLE_RANK[str(minimum)]
