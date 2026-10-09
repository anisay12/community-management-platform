"""Context of the membership action buttons shown in a community header."""

from django import template
from django.utils import timezone

from .. import policies
from ..forms_actions import REQUEST_MESSAGE_MAX_LENGTH
from ..models import CommunityInvitation, MembershipRequest

register = template.Library()


@register.simple_tag
def community_actions(user, community):
    """What ``user`` can do about their membership of ``community``."""
    membership = policies.membership_of(user, community)
    state = {"membership": membership, "max_length": REQUEST_MESSAGE_MAX_LENGTH}
    if membership is not None or not getattr(user, "is_authenticated", False):
        return state
    state["invitation"] = CommunityInvitation.objects.filter(
        community=community,
        invited_user=user,
        status=CommunityInvitation.Status.PENDING,
        expires_at__gt=timezone.now(),
    ).first()
    state["pending_request"] = MembershipRequest.objects.filter(
        community=community, user=user, status=MembershipRequest.Status.PENDING
    ).exists()
    state["can_join"] = policies.can_join(user, community)
    return state
