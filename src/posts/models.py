"""Posts, comments and the interactions around them (reactions, mentions, bookmarks, reports).

Bodies are stored as Markdown (``body``) and rendered once, at write time, into ``body_html``
(``posts.rendering.render_body``). Counters (``comment_count``, ``reaction_counts``) are
recomputed by ``posts.tasks.recount_target``.
"""

import functools
import operator
import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.validators import MaxLengthValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from communities.models import TimestampedModel

POST_BODY_MAX_LENGTH = 50_000
COMMENT_BODY_MAX_LENGTH = 10_000
REPORT_DETAILS_MAX_LENGTH = 2_000


def _exactly_one(*fields: str) -> models.Q:
    """``Q`` true when exactly one of the nullable foreign keys ``fields`` is set."""
    branches = []
    for field in fields:
        branch = models.Q(**{f"{field}__isnull": False})
        for other in fields:
            if other != field:
                branch &= models.Q(**{f"{other}__isnull": True})
        branches.append(branch)
    return functools.reduce(operator.or_, branches)


class PostQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Posts ``user`` may open (see ``posts.selectors.visible_posts_q``)."""
        from .selectors import visible_posts_q

        condition = visible_posts_q(user)
        return self.none() if condition is None else self.filter(condition)


class Post(TimestampedModel):
    class Kind(models.TextChoices):
        DISCUSSION = "discussion", _("Discussion")
        QUESTION = "question", _("Question")
        ANNOUNCEMENT = "announcement", _("Announcement")
        ARTICLE = "article", _("Article")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        PENDING_REVIEW = "pending_review", _("Pending review")
        PUBLISHED = "published", _("Published")
        HIDDEN = "hidden", _("Hidden")
        ARCHIVED = "archived", _("Archived")

    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    community = models.ForeignKey(
        "communities.Community",
        on_delete=models.PROTECT,
        related_name="posts",
        verbose_name=_("community"),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="posts",
        verbose_name=_("author"),
    )
    author_display = models.CharField(_("author name"), max_length=150)
    kind = models.CharField(_("kind"), max_length=16, choices=Kind.choices, default=Kind.DISCUSSION)
    title = models.CharField(_("title"), max_length=200)
    body = models.TextField(_("body"), validators=[MaxLengthValidator(POST_BODY_MAX_LENGTH)])
    body_html = models.TextField(_("rendered body"), blank=True)
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    published_at = models.DateTimeField(_("published at"), null=True, blank=True)
    pinned_at = models.DateTimeField(_("pinned at"), null=True, blank=True)
    pinned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("pinned by"),
    )
    accepted_answer = models.ForeignKey(
        "Comment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("accepted answer"),
    )
    tags = models.ManyToManyField(
        "taxonomy.Tag", blank=True, related_name="posts", verbose_name=_("tags")
    )
    shared_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shares",
        verbose_name=_("shared from"),
    )
    last_activity_at = models.DateTimeField(_("last activity"), default=timezone.now)
    comment_count = models.PositiveIntegerField(_("comment count"), default=0)
    reaction_counts = models.JSONField(_("reaction counts"), default=dict, blank=True)
    version = models.PositiveIntegerField(_("version"), default=1)
    hidden_at = models.DateTimeField(_("hidden at"), null=True, blank=True)
    hidden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("hidden by"),
    )
    hidden_reason = models.TextField(_("reason for hiding"), blank=True)
    status_before_hidden = models.CharField(
        _("status before hiding"), max_length=16, choices=Status.choices, blank=True
    )
    review_note = models.TextField(_("review note"), blank=True)
    archived_at = models.DateTimeField(_("archived at"), null=True, blank=True)
    search_vector = SearchVectorField(_("search vector"), null=True, editable=False)

    objects = PostQuerySet.as_manager()

    class Meta:
        ordering = ["-last_activity_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(accepted_answer__isnull=True) | models.Q(kind="question"),
                name="post_accepted_answer_only_question",
            ),
        ]
        indexes = [
            models.Index(
                "community", "status", models.F("last_activity_at").desc(), name="post_feed_idx"
            ),
            models.Index(
                fields=["community", "pinned_at"],
                condition=models.Q(pinned_at__isnull=False),
                name="post_pinned_idx",
            ),
            models.Index("author", models.F("created_at").desc(), name="post_author_idx"),
            GinIndex(fields=["search_vector"], name="post_search_gin"),
        ]
        verbose_name = _("post")
        verbose_name_plural = _("posts")

    def __str__(self) -> str:
        return self.title


class PostRevision(models.Model):
    """The title and body of a post before an edit (see the revision rule in the services)."""

    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, related_name="revisions", verbose_name=_("post")
    )
    editor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("editor"),
    )
    title = models.CharField(_("title"), max_length=200)
    body = models.TextField(_("body"))
    created_at = models.DateTimeField(_("created at"), default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("post revision")
        verbose_name_plural = _("post revisions")

    def __str__(self) -> str:
        return f"{self.post_id} @ {self.created_at:%Y-%m-%d %H:%M}"


class Comment(TimestampedModel):
    class Status(models.TextChoices):
        VISIBLE = "visible", _("Visible")
        HIDDEN = "hidden", _("Hidden")

    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    post = models.ForeignKey(
        Post, on_delete=models.PROTECT, related_name="comments", verbose_name=_("post")
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="comments",
        verbose_name=_("author"),
    )
    author_display = models.CharField(_("author name"), max_length=150)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="replies",
        verbose_name=_("parent"),
    )
    body = models.TextField(_("body"), validators=[MaxLengthValidator(COMMENT_BODY_MAX_LENGTH)])
    body_html = models.TextField(_("rendered body"), blank=True)
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.VISIBLE
    )
    is_expert_answer = models.BooleanField(_("expert answer"), default=False)
    hidden_at = models.DateTimeField(_("hidden at"), null=True, blank=True)
    hidden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("hidden by"),
    )
    hidden_reason = models.TextField(_("reason for hiding"), blank=True)
    reaction_counts = models.JSONField(_("reaction counts"), default=dict, blank=True)

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [
            models.Index(fields=["post", "parent", "created_at"], name="comment_thread_idx"),
        ]
        verbose_name = _("comment")
        verbose_name_plural = _("comments")

    def __str__(self) -> str:
        return f"{self.public_id} on {self.post_id}"


class Reaction(models.Model):
    class Kind(models.TextChoices):
        USEFUL = "useful", _("Useful")
        THANKS = "thanks", _("Thanks")
        INSIGHTFUL = "insightful", _("Insightful")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reactions",
        verbose_name=_("user"),
    )
    post = models.ForeignKey(
        Post,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="reactions",
        verbose_name=_("post"),
    )
    comment = models.ForeignKey(
        Comment,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="reactions",
        verbose_name=_("comment"),
    )
    kind = models.CharField(_("kind"), max_length=16, choices=Kind.choices)
    created_at = models.DateTimeField(_("created at"), default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=_exactly_one("post", "comment"), name="reaction_exactly_one_target"
            ),
            models.UniqueConstraint(
                fields=["user", "post", "kind"],
                condition=models.Q(post__isnull=False),
                name="reaction_unique_post",
            ),
            models.UniqueConstraint(
                fields=["user", "comment", "kind"],
                condition=models.Q(comment__isnull=False),
                name="reaction_unique_comment",
            ),
        ]
        verbose_name = _("reaction")
        verbose_name_plural = _("reactions")

    def __str__(self) -> str:
        return f"{self.user_id} {self.kind}"


class Mention(models.Model):
    post = models.ForeignKey(
        Post,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="mentions",
        verbose_name=_("post"),
    )
    comment = models.ForeignKey(
        Comment,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="mentions",
        verbose_name=_("comment"),
    )
    mentioned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mentions",
        verbose_name=_("mentioned user"),
    )
    created_at = models.DateTimeField(_("created at"), default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=_exactly_one("post", "comment"), name="mention_exactly_one_source"
            ),
            models.UniqueConstraint(
                fields=["post", "mentioned_user"],
                condition=models.Q(post__isnull=False),
                name="mention_unique_post",
            ),
            models.UniqueConstraint(
                fields=["comment", "mentioned_user"],
                condition=models.Q(comment__isnull=False),
                name="mention_unique_comment",
            ),
        ]
        verbose_name = _("mention")
        verbose_name_plural = _("mentions")

    def __str__(self) -> str:
        return f"@{self.mentioned_user_id}"


class BookmarkCollection(TimestampedModel):
    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bookmark_collections",
        verbose_name=_("user"),
    )
    name = models.CharField(_("name"), max_length=80)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                models.F("user"), Lower("name"), name="bookmark_collection_name_unique"
            ),
        ]
        verbose_name = _("bookmark collection")
        verbose_name_plural = _("bookmark collections")

    def __str__(self) -> str:
        return self.name


class Bookmark(models.Model):
    """A saved item. L4 bookmarks posts only; L5 and L6 add their target and widen the CHECK."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bookmarks",
        verbose_name=_("user"),
    )
    post = models.ForeignKey(
        Post,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="bookmarks",
        verbose_name=_("post"),
    )
    collection = models.ForeignKey(
        BookmarkCollection,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bookmarks",
        verbose_name=_("collection"),
    )
    created_at = models.DateTimeField(_("created at"), default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=_exactly_one("post"), name="bookmark_exactly_one_target"
            ),
            models.UniqueConstraint(
                fields=["user", "post"],
                condition=models.Q(post__isnull=False),
                name="bookmark_unique_post",
            ),
        ]
        verbose_name = _("bookmark")
        verbose_name_plural = _("bookmarks")

    def __str__(self) -> str:
        return f"{self.user_id} -> {self.post_id}"


class ContentReport(TimestampedModel):
    class Reason(models.TextChoices):
        INAPPROPRIATE = "inappropriate", _("Inappropriate content")
        CONFIDENTIAL = "confidential", _("Confidential information")
        OUTDATED = "outdated", _("Outdated information")
        SPAM = "spam", _("Spam")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        RESOLVED = "resolved", _("Resolved")
        DISMISSED = "dismissed", _("Dismissed")

    public_id = models.UUIDField(_("public ID"), default=uuid.uuid4, unique=True, editable=False)
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="content_reports",
        verbose_name=_("reporter"),
    )
    post = models.ForeignKey(
        Post,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reports",
        verbose_name=_("post"),
    )
    comment = models.ForeignKey(
        Comment,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reports",
        verbose_name=_("comment"),
    )
    community = models.ForeignKey(
        "communities.Community",
        on_delete=models.PROTECT,
        related_name="content_reports",
        verbose_name=_("community"),
    )
    reason = models.CharField(_("reason"), max_length=16, choices=Reason.choices)
    details = models.TextField(_("details"), max_length=REPORT_DETAILS_MAX_LENGTH, blank=True)
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.OPEN
    )
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name=_("handled by"),
    )
    handled_at = models.DateTimeField(_("handled at"), null=True, blank=True)
    resolution_note = models.TextField(_("resolution note"), blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=_exactly_one("post", "comment"), name="report_exactly_one_target"
            ),
            models.UniqueConstraint(
                fields=["reporter", "post"],
                condition=models.Q(status="open", post__isnull=False),
                name="one_open_report_per_target_post",
            ),
            models.UniqueConstraint(
                fields=["reporter", "comment"],
                condition=models.Q(status="open", comment__isnull=False),
                name="one_open_report_per_target_comment",
            ),
        ]
        indexes = [
            models.Index(fields=["community", "status", "created_at"], name="report_queue_idx"),
        ]
        verbose_name = _("content report")
        verbose_name_plural = _("content reports")

    def __str__(self) -> str:
        return f"{self.reason} ({self.status})"
