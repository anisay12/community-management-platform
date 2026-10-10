"""Content to review (spec L5, lifecycle): the documents a manager should check, because their
review date has passed or a reader reported them as outdated. Read by the lead dashboard."""

from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from posts.models import ContentReport

from .models import Document
from .selectors import manager_documents_q


def _open_outdated_report():
    return ContentReport.objects.filter(
        document=OuterRef("pk"),
        status=ContentReport.Status.OPEN,
        reason=ContentReport.Reason.OUTDATED,
    )


def documents_to_review(user, community=None):
    """Active documents ``user`` manages (owner, moderator+, functional admin with content
    access) whose ``review_due_at`` has passed or that have an open ``outdated`` report;
    optionally in ``community`` only. Ordered by review date (the oldest first, documents
    without a review date last).

    Each document carries ``has_outdated_report`` (bool).
    """
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return Document.objects.none()
    queryset = (
        Document.objects.visible_to(user)
        .filter(manager_documents_q(user), status=Document.Status.ACTIVE)
        .annotate(has_outdated_report=Exists(_open_outdated_report()))
        .filter(Q(review_due_at__lte=timezone.now()) | Q(has_outdated_report=True))
    )
    if community is not None:
        queryset = queryset.filter(community=community)
    return queryset.select_related("community", "owner").order_by(
        F("review_due_at").asc(nulls_last=True), "pk"
    )
