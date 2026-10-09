"""Read-side queries on posts. Visibility is decided in SQL here and mirrored by
``policies.can_view_post`` (the visibility tests check that they agree)."""

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounts.roles import Role, has_role
from communities.models import AdminAccessGrant, Community, CommunityMembership
from communities.policies import is_functional_admin
from communities.roles import ROLE_RANK, CommunityRole

from .models import Post

MODERATOR_ROLES = [
    role for role, rank in ROLE_RANK.items() if rank >= ROLE_RANK[CommunityRole.MODERATOR]
]


def _content_community_q(user, prefix: str = "community__") -> Q:
    """``Q`` on posts whose community content ``user`` may read (``can_view_content`` in SQL)."""
    member_ids = CommunityMembership.objects.filter(user=user).values("community_id")
    open_and_live = Q(**{f"{prefix}access_mode": Community.AccessMode.OPEN}) & ~Q(
        **{f"{prefix}status": Community.Status.ARCHIVED}
    )
    condition = Q(**{f"{prefix}pk__in": member_ids})
    if is_functional_admin(user):
        grant_ids = AdminAccessGrant.objects.filter(
            user=user, expires_at__gt=timezone.now()
        ).values("community_id")
        return condition | open_and_live | Q(**{f"{prefix}pk__in": grant_ids})
    if has_role(user, Role.TECHNICAL_ADMIN) or has_role(user, Role.AUDITOR):
        return condition
    return condition | open_and_live


def visible_posts_q(user) -> Q | None:
    """``Q`` of the posts ``user`` may open, or ``None`` when they may open none.

    Published and archived posts of the communities whose content the user reads; the
    user's own drafts, pending and hidden posts; pending and hidden posts of the communities
    where the user moderates (moderator+, or functional admin with content access).
    """
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    Status = Post.Status
    content = _content_community_q(user)
    moderated_ids = CommunityMembership.objects.filter(user=user, role__in=MODERATOR_ROLES).values(
        "community_id"
    )
    moderated = Q(community__in=moderated_ids)
    if is_functional_admin(user):
        moderated |= content
    return (
        (Q(status__in=[Status.PUBLISHED, Status.ARCHIVED]) & content)
        | Q(author=user, status__in=[Status.DRAFT, Status.PENDING_REVIEW, Status.HIDDEN])
        | (Q(status__in=[Status.PENDING_REVIEW, Status.HIDDEN]) & moderated)
    )


def get_visible_post_or_404(user, community_slug, public_id) -> Post:
    """The post ``public_id`` of community ``community_slug`` if ``user`` may open it."""
    return get_object_or_404(
        Post.objects.visible_to(user).select_related("community", "author"),
        community__slug=community_slug,
        public_id=public_id,
    )
