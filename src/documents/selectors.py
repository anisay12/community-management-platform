"""Read-side queries on documents. Visibility is decided in SQL here and mirrored by
``policies.can_view_document`` (the visibility tests check that they agree)."""

from django.db.models import Q
from django.shortcuts import get_object_or_404

from communities.models import CommunityMembership
from communities.policies import is_functional_admin
from communities.roles import ROLE_RANK, CommunityRole

# Same rule as the posts: which communities' content the user reads (``can_view_content``).
from posts.selectors import _content_community_q

from .models import Document, DocumentVersion

MODERATOR_ROLES = [
    role for role, rank in ROLE_RANK.items() if rank >= ROLE_RANK[CommunityRole.MODERATOR]
]


def roles_at_least(minimum) -> list[str]:
    return [str(role) for role, rank in ROLE_RANK.items() if rank >= ROLE_RANK[str(minimum)]]


def _member_with_role_q(user, minimum, prefix: str = "community_id__in") -> Q:
    community_ids = CommunityMembership.objects.filter(
        user=user, role__in=roles_at_least(minimum)
    ).values("community_id")
    return Q(**{prefix: community_ids})


def manager_documents_q(user) -> Q:
    """``Q`` of the documents ``user`` manages (``policies.is_document_manager``), assuming the
    caller already restricts to communities whose content ``user`` reads."""
    condition = Q(owner=user) | _member_with_role_q(user, CommunityRole.MODERATOR)
    if is_functional_admin(user):
        condition |= Q(pk__isnull=False)
    return condition


def visible_documents_q(user) -> Q | None:
    """``Q`` of the documents ``user`` may open, or ``None`` when they may open none.

    In the communities whose content the user reads: active documents with a clean reference
    version (restricted ones only for members with ``min_role`` or above), plus every document
    the user manages (owner, moderator+, functional admin with content access).
    """
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    read_role = Q(visibility=Document.Visibility.COMMUNITY)
    for role in CommunityRole.values:
        read_role |= Q(visibility=Document.Visibility.RESTRICTED, min_role=role) & (
            _member_with_role_q(user, role)
        )
    readable = (
        Q(status=Document.Status.ACTIVE)
        & Q(current_version__scan_status=DocumentVersion.ScanStatus.CLEAN)
        & read_role
    )
    return _content_community_q(user) & (readable | manager_documents_q(user))


def get_visible_document_or_404(user, public_id) -> Document:
    """The document ``public_id`` if ``user`` may open it (404 otherwise)."""
    return get_object_or_404(
        Document.objects.visible_to(user).select_related("community", "owner", "current_version"),
        public_id=public_id,
    )
