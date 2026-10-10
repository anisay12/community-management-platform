"""Read side of the moderation queue: reports grouped by target, pending posts, counts."""

from dataclasses import dataclass, field

from django.db.models import (
    BooleanField,
    Case,
    Count,
    F,
    IntegerField,
    Max,
    Q,
    Subquery,
    Value,
    When,
    Window,
)
from django.db.models.functions import Coalesce, RowNumber

from communities.models import Community

from .hiding import is_auto_hidden
from .models import Comment, ContentReport, Post

# Reports listed under one target in the queue (the most recent); the count shows them all.
REPORTS_SHOWN_PER_GROUP = 10


@dataclass
class ReportGroup:
    """The reports of one target (a post or a comment), open or already handled."""

    target: object
    is_open: bool
    report_count: int
    latest: object
    reports: list = field(default_factory=list)

    @property
    def is_post(self) -> bool:
        return isinstance(self.target, Post)

    @property
    def post(self) -> Post:
        return self.target if self.is_post else self.target.post

    @property
    def auto_hidden(self) -> bool:
        return is_auto_hidden(self.target)

    @property
    def hidden_count(self) -> int:
        """Older reports of the group that are not listed."""
        return max(self.report_count - len(self.reports), 0)

    @property
    def action_report(self) -> ContentReport:
        """The report the decision form posts to (the decision covers the whole group)."""
        return self.reports[0]

    @property
    def reasons(self) -> list[str]:
        """Distinct reason labels, in the order of the reports."""
        return list(dict.fromkeys(str(report.get_reason_display()) for report in self.reports))


def report_groups(community):
    """A values queryset of ``(post_id, comment_id, is_open)`` groups with ``report_count``
    and ``latest``: groups with open reports first, then the most recent."""
    return (
        ContentReport.objects.filter(community=community)
        .annotate(is_open=_is_open_expression())
        .values("post_id", "comment_id", "is_open")
        .annotate(report_count=Count("pk"), latest=Max("created_at"))
        .order_by("-is_open", "-latest", "post_id", "comment_id")
    )


def _is_open_expression() -> Case:
    return Case(
        When(status=ContentReport.Status.OPEN, then=Value(True)),
        default=Value(False),
        output_field=BooleanField(),
    )


def load_report_groups(community, rows) -> list[ReportGroup]:
    """Turn a page of ``report_groups`` rows into ``ReportGroup`` objects (three queries:
    posts, comments, reports). Each group lists its ``REPORTS_SHOWN_PER_GROUP`` most recent
    reports, oldest first; ``report_count`` keeps the full number."""
    rows = list(rows)
    post_ids = {row["post_id"] for row in rows if row["post_id"]}
    comment_ids = {row["comment_id"] for row in rows if row["comment_id"]}
    posts = Post.objects.in_bulk(post_ids)
    comments = Comment.objects.select_related("post").in_bulk(comment_ids)
    groups = {}
    for row in rows:
        target = posts[row["post_id"]] if row["post_id"] else comments[row["comment_id"]]
        key = (row["post_id"], row["comment_id"], row["is_open"])
        groups[key] = ReportGroup(
            target=target,
            is_open=row["is_open"],
            report_count=row["report_count"],
            latest=row["latest"],
        )
    recent_first = Window(
        RowNumber(),
        partition_by=[F("post_id"), F("comment_id"), _is_open_expression()],
        order_by=[F("created_at").desc(), F("pk").desc()],
    )
    reports = (
        ContentReport.objects.filter(community=community)
        .filter(Q(post_id__in=post_ids) | Q(comment_id__in=comment_ids))
        .annotate(rank=recent_first)
        .filter(rank__lte=REPORTS_SHOWN_PER_GROUP)
        .order_by("created_at", "pk")
    )
    for report in reports:
        key = (report.post_id, report.comment_id, report.status == ContentReport.Status.OPEN)
        if key in groups:
            groups[key].reports.append(report)
    return list(groups.values())


def pending_posts(community):
    return Post.objects.filter(community=community, status=Post.Status.PENDING_REVIEW).order_by(
        "created_at", "pk"
    )


def _count(queryset, expression) -> Coalesce:
    """A scalar subquery counting ``expression`` over ``queryset`` (0 when empty)."""
    counted = (
        queryset.order_by()
        .annotate(group=Value(1))
        .values("group")
        .annotate(total=expression)
        .values("total")
    )
    return Coalesce(Subquery(counted, output_field=IntegerField()), 0)


def queue_counts(community) -> tuple[int, int]:
    """``(reported, pending)`` in one query: posts and comments of ``community`` with at least
    one open report, and posts awaiting review."""
    open_reports = ContentReport.objects.filter(
        community=community, status=ContentReport.Status.OPEN
    )
    row = (
        Community.objects.filter(pk=community.pk)
        .annotate(
            reported_posts=_count(open_reports, Count("post", distinct=True)),
            reported_comments=_count(open_reports, Count("comment", distinct=True)),
            pending=_count(pending_posts(community), Count("pk")),
        )
        .values("reported_posts", "reported_comments", "pending")
        .get()
    )
    return row["reported_posts"] + row["reported_comments"], row["pending"]
