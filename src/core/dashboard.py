"""Context of the signed-in home page: a role-based dashboard built from existing data.

Every figure is an aggregate (one query per block), scoped by the existing visibility rules
(``visible_to``) and permission policies, so the page cost does not grow with the data.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.policies import can_manage_users
from accounts.roles import Role, has_role
from audit.policies import can_view_audit_log
from audit.selectors import events_visible_to
from communities.models import Community, CommunityCreationRequest, CommunityInvitation
from communities.selectors import visible_communities
from documents.models import Document
from documents.selectors_pages import latest_scan_errors, recent_member_documents
from documents.selectors_review import documents_to_review
from posts.models import Bookmark, ContentReport, Post
from posts.selectors import MODERATOR_ROLES, home_feed

MY_COMMUNITIES_LIMIT = 6
LATEST_POSTS_LIMIT = 5
AUDIT_WINDOW_DAYS = 7
DOCUMENTS_LIMIT = 5


def _subquery_count(queryset):
    """A scalar subquery counting the rows of ``queryset`` (0 when empty)."""
    counted = queryset.order_by().values("community").annotate(total=Count("pk")).values("total")
    return Coalesce(Subquery(counted, output_field=IntegerField()), 0)


def _status_counts(queryset, choices) -> list[dict]:
    """``[{"status", "label", "count"}]`` for every status of ``choices`` (one query)."""
    counts = dict(queryset.order_by().values_list("status").annotate(total=Count("pk")))
    return [
        {"status": value, "label": label, "count": counts.get(value, 0)} for value, label in choices
    ]


def _my_communities(user) -> tuple[list, int]:
    queryset = visible_communities(user).filter(memberships__user=user).distinct()
    communities = list(queryset.order_by("-last_activity_at", "pk")[:MY_COMMUNITIES_LIMIT])
    if len(communities) < MY_COMMUNITIES_LIMIT:
        return communities, len(communities)
    return communities, queryset.count()


def _moderation(user) -> list[Community]:
    """Communities ``user`` moderates, with their pending posts and open reports counts."""
    pending = Post.objects.filter(community=OuterRef("pk"), status=Post.Status.PENDING_REVIEW)
    reports = ContentReport.objects.filter(
        community=OuterRef("pk"), status=ContentReport.Status.OPEN
    )
    return list(
        Community.objects.filter(memberships__user=user, memberships__role__in=MODERATOR_ROLES)
        .annotate(pending_count=_subquery_count(pending), report_count=_subquery_count(reports))
        .order_by("name", "pk")
    )


def _administration() -> dict:
    User = get_user_model()
    return {
        "users": _status_counts(User.objects.all(), User.Status.choices),
        "communities": _status_counts(Community.objects.all(), Community.Status.choices),
        "pending_creation_requests": CommunityCreationRequest.objects.filter(
            status=CommunityCreationRequest.Status.PENDING
        ).count(),
    }


def _is_admin(user) -> bool:
    """Functional or technical administrators (and superusers) see the scan errors."""
    return bool(
        user.is_superuser
        or has_role(user, Role.FUNCTIONAL_ADMIN)
        or has_role(user, Role.TECHNICAL_ADMIN)
    )


def _scan_errors(user) -> list:
    """The latest failed or infected scans; ``openable`` tells whether ``user`` may open the
    document (two queries whatever the number of rows)."""
    versions = latest_scan_errors(DOCUMENTS_LIMIT)
    openable = set(
        Document.objects.visible_to(user)
        .filter(pk__in={version.document_id for version in versions})
        .values_list("pk", flat=True)
    )
    for version in versions:
        version.openable = version.document_id in openable
    return versions


def dashboard_context(user) -> dict:
    """The dashboard blocks ``user`` may see (an empty dict for anonymous users)."""
    if not getattr(user, "is_authenticated", False):
        return {}
    communities, community_count = _my_communities(user)
    visible_posts = Post.objects.visible_to(user).values("pk")
    context = {
        "my_communities": communities,
        "my_community_count": community_count,
        "bookmark_count": Bookmark.objects.filter(user=user, post__in=visible_posts).count(),
        "invitation_count": CommunityInvitation.objects.filter(
            invited_user=user,
            status=CommunityInvitation.Status.PENDING,
            expires_at__gt=timezone.now(),
            community__status=Community.Status.ACTIVE,
        ).count(),
        "latest_posts": home_feed(user).items[:LATEST_POSTS_LIMIT],
    }
    context["recent_documents"] = recent_member_documents(user, DOCUMENTS_LIMIT)
    to_review = list(documents_to_review(user)[:DOCUMENTS_LIMIT])
    now = timezone.now()
    for document in to_review:
        document.review_passed = bool(document.review_due_at and document.review_due_at <= now)
    if to_review:
        context["documents_to_review"] = to_review
    if _is_admin(user):
        context["scan_errors"] = _scan_errors(user)
    moderated = _moderation(user)
    if moderated:
        context["moderation"] = {
            "communities": moderated,
            "pending": sum(community.pending_count for community in moderated),
            "reports": sum(community.report_count for community in moderated),
        }
    if can_manage_users(user):
        context["administration"] = _administration()
    if can_view_audit_log(user):
        since = timezone.now() - timedelta(days=AUDIT_WINDOW_DAYS)
        context["audit_recent_count"] = (
            events_visible_to(user).filter(created_at__gte=since).order_by().count()
        )
    return context
