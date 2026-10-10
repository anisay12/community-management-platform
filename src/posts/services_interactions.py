"""Write services on interactions (Task 3): comments, reactions, bookmarks and collections,
content reports.

Every service is atomic, keyword-only with the actor first, checks its policy itself and
raises ``core.errors.DomainError`` (``forbidden``, ``own_content``, ``already_reported``, ...).
Specific refusals (``read_only``, ``invalid_state``, ``not_member``, ``own_content``) are
checked before the policy, whose ``False`` then means ``forbidden``.
"""

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from audit.services import record
from communities.models import Community, CommunityMembership
from communities.policies import membership_of
from communities.roles import ROLE_RANK, CommunityRole, role_at_least
from core.errors import DomainError
from notifications.services import notify

from . import policies
from .hiding import confirm_hidden, is_auto_hidden, mark_hidden, mark_visible
from .mentions import resolve_mentions, sync_mentions
from .models import (
    COMMENT_BODY_MAX_LENGTH,
    REPORT_DETAILS_MAX_LENGTH,
    Bookmark,
    BookmarkCollection,
    Comment,
    ContentReport,
    Post,
    Reaction,
)
from .privacy import author_display_for
from .ratelimit import hit
from .rendering import render_body
from .tasks import schedule_recount

COLLECTION_NAME_MAX_LENGTH = 80
MODERATOR_ROLES = [role for role in CommunityRole if ROLE_RANK[role] >= ROLE_RANK["moderator"]]


# Errors ----------------------------------------------------------------------------


def _forbidden():
    return DomainError("forbidden", _("You are not allowed to perform this action."))


def _invalid_state():
    return DomainError("invalid_state", _("This action is not possible in the current state."))


def _read_only():
    return DomainError(
        "read_only", _("This community is suspended or archived: it cannot be changed.")
    )


def _not_member():
    return DomainError("not_member", _("Join this community to take part."))


def _own_content():
    return DomainError("own_content", _("You cannot do this on your own content."))


# Helpers ---------------------------------------------------------------------------


def _is_author(user, content) -> bool:
    return content.author_id is not None and content.author_id == user.pk


def _post_of(target) -> Post:
    return target.post if isinstance(target, Comment) else target


def _can_view_target(user, target) -> bool:
    """Read access to a post, or to a comment (a hidden one only for its author and the
    moderators): a refusal answers 404, never revealing that the target exists."""
    if isinstance(target, Comment):
        return policies.can_view_comment(user, target)
    return policies.can_view_post(user, target)


def _is_open(target) -> bool:
    """Published post, or visible comment of a published post."""
    post = _post_of(target)
    if post.status != Post.Status.PUBLISHED:
        return False
    return not isinstance(target, Comment) or target.status == Comment.Status.VISIBLE


def _ensure_participation(actor, post: Post) -> None:
    """Read access, a writable community and a membership, in that order of refusal."""
    if not policies.can_view_post(actor, post):
        raise _forbidden()
    if post.community.is_read_only:
        raise _read_only()
    if membership_of(actor, post.community) is None:
        raise _not_member()


def _clean_body(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise DomainError("body_required", _("Write something first."))
    if len(body) > COMMENT_BODY_MAX_LENGTH:
        raise DomainError(
            "body_too_long",
            _("This text is too long (%(max)s characters at most).")
            % {"max": COMMENT_BODY_MAX_LENGTH},
        )
    return body


def _touch(post: Post) -> None:
    """New activity on ``post`` and its community (in the business transaction)."""
    now = timezone.now()
    Post.objects.filter(pk=post.pk).update(last_activity_at=now)
    Community.objects.filter(pk=post.community_id).update(last_activity_at=now)
    post.last_activity_at = now


def _notify_mentions(actor, comment: Comment) -> None:
    post = comment.post
    users = [
        user
        for user in resolve_mentions(post.community, comment.body)
        if user.pk != actor.pk and policies.can_view_post(user, post)
    ]
    new_users = sync_mentions(comment, users)
    notify("mention", new_users, actor=actor, target=comment, community=post.community)


def _moderators(community):
    return [
        membership.user
        for membership in CommunityMembership.objects.filter(
            community=community, role__in=MODERATOR_ROLES
        ).select_related("user")
    ]


def _lock(obj):
    return type(obj).objects.select_for_update().get(pk=obj.pk)


# Comments --------------------------------------------------------------------------


@transaction.atomic
def add_comment(*, actor, post, body, parent=None):
    """Comment ``post`` (or answer ``parent``; a reply to a reply goes under its top-level
    comment). Notifies the author of the post or comment answered (``reply``) and the newly
    mentioned members (``mention``)."""
    _ensure_participation(actor, post)
    if post.status != Post.Status.PUBLISHED:
        raise _invalid_state()
    if not policies.can_comment(actor, post):
        raise _forbidden()
    answered = post.author
    if parent is not None:
        if parent.post_id != post.pk:
            raise DomainError("depth_exceeded", _("This reply does not belong to this post."))
        answered = parent.author  # the person answered, even when the reply is flattened
        # Both the comment answered and, for a reply, its top-level comment must be visible.
        if parent.status != Comment.Status.VISIBLE:
            raise _invalid_state()
        if parent.parent_id is not None:
            parent = parent.parent
            if parent.status != Comment.Status.VISIBLE:
                raise _invalid_state()
    body = _clean_body(body)
    hit(actor, "comment")
    membership = membership_of(actor, post.community)
    comment = Comment.objects.create(
        post=post,
        author=actor,
        author_display=author_display_for(actor),
        parent=parent,
        body=body,
        body_html=render_body(body),
        is_expert_answer=role_at_least(membership.role, CommunityRole.EXPERT),
    )
    _touch(post)
    # Only someone who can still read the post (e.g. not a former member of a private
    # community) hears about the reply.
    if answered is not None and policies.can_view_comment(answered, comment):
        notify("reply", [answered], actor=actor, target=comment, community=post.community)
    _notify_mentions(actor, comment)
    schedule_recount(post)
    return comment


@transaction.atomic
def update_comment(*, actor, comment, body):
    """Edit a comment: its author, or a moderator+ (no revision kept, last write wins).

    A moderator's edit of someone else's comment is audited (``comment.moderator_edited``)
    and tells its author (``system``) if they can still read the post.
    """
    post = comment.post
    if not policies.can_view_comment(actor, comment):
        raise _forbidden()
    if post.community.is_read_only:
        raise _read_only()
    if post.status == Post.Status.ARCHIVED:
        raise _invalid_state()  # archived posts are read-only, for moderators too
    moderating = policies.is_content_moderator(actor, post.community)
    if not (moderating or policies.can_edit_comment(actor, comment)):
        raise _forbidden()
    body = _clean_body(body)
    comment.body = body
    comment.body_html = render_body(body)
    comment.save(update_fields=["body", "body_html", "updated_at"])
    _notify_mentions(actor, comment)
    if not _is_author(actor, comment):
        record(
            actor=actor,
            action="comment.moderator_edited",
            target=comment,
            community=post.community,
        )
        _notify_author(actor, comment)
    return comment


def _notify_author(actor, comment: Comment) -> None:
    """``system`` notification to the comment's author, if they can still read the post."""
    author = comment.author
    if author is not None and policies.can_view_post(author, comment.post):
        notify("system", [author], actor=actor, target=comment, community=comment.post.community)


def _ensure_moderator(actor, comment: Comment) -> None:
    community = comment.post.community
    if not (
        policies.can_view_post(actor, comment.post) and policies.can_moderate(actor, community)
    ):
        raise _forbidden()


@transaction.atomic
def hide_comment(*, actor, comment, reason):
    """Hide a comment (audited ``comment.hidden`` with the reason) and tell its author if they
    can still read the post."""
    _ensure_moderator(actor, comment)
    mark_hidden(comment, actor=actor, reason=reason)
    _notify_author(actor, comment)
    schedule_recount(comment.post)
    return comment


@transaction.atomic
def unhide_comment(*, actor, comment):
    _ensure_moderator(actor, comment)
    mark_visible(comment, actor=actor)
    schedule_recount(comment.post)
    return comment


# Reactions -------------------------------------------------------------------------


@transaction.atomic
def set_reaction(*, actor, target, kind, present: bool) -> bool:
    """Make the ``kind`` reaction of ``actor`` to a post or comment present or absent.

    Idempotent: the request states the wanted outcome, so a repeated or concurrent request
    (double click, two tabs) leaves the same state. Returns ``present``.
    """
    post = _post_of(target)
    if not _can_view_target(actor, target):
        raise _forbidden()
    if _is_author(actor, target):
        raise _own_content()
    if kind not in Reaction.Kind.values or not _is_open(target):
        raise _invalid_state()
    _ensure_participation(actor, post)
    if not policies.can_react(actor, target):
        raise _forbidden()
    hit(actor, "reaction")
    field = "comment" if isinstance(target, Comment) else "post"
    lookup = {"user": actor, field: target, "kind": kind}
    if present:
        try:
            with transaction.atomic():
                _row, changed = Reaction.objects.get_or_create(**lookup)
        except IntegrityError:
            changed = False  # added concurrently by the same user: the outcome is identical
    else:
        changed, _rows = Reaction.objects.filter(**lookup).delete()
    if changed:
        schedule_recount(target)
    return present


# Bookmarks -------------------------------------------------------------------------


def _ensure_owner(actor, obj) -> None:
    active = getattr(actor, "is_authenticated", False) and actor.is_active
    if not active or (obj is not None and obj.user_id != actor.pk):
        raise _forbidden()


@transaction.atomic
def set_bookmark(*, actor, post, present: bool, collection=None) -> bool:
    """Bookmark a post the actor can read (optionally in one of their collections), or remove
    the bookmark. Idempotent like ``set_reaction``; returns ``present``. An existing bookmark
    stays in its collection (``move_bookmark`` moves it).

    Removing needs no read access: a former member can still clean up their bookmarks.
    """
    _ensure_owner(actor, collection)
    if not present:
        Bookmark.objects.filter(user=actor, post=post).delete()
        return False
    if not policies.can_bookmark(actor, post):
        raise _forbidden()
    try:
        with transaction.atomic():
            Bookmark.objects.get_or_create(
                user=actor, post=post, defaults={"collection": collection}
            )
    except IntegrityError:
        pass  # bookmarked concurrently
    return True


def _clean_name(actor, name: str, *, exclude_pk=None) -> str:
    name = (name or "").strip()
    if not name:
        raise DomainError("name_required", _("Give this collection a name."))
    if len(name) > COLLECTION_NAME_MAX_LENGTH:
        raise DomainError(
            "name_too_long",
            _("This name is too long (%(max)s characters at most).")
            % {"max": COLLECTION_NAME_MAX_LENGTH},
        )
    taken = BookmarkCollection.objects.filter(user=actor, name__iexact=name).exclude(pk=exclude_pk)
    if taken.exists():
        raise _name_taken()
    return name


def _name_taken():
    return DomainError("name_taken", _("You already have a collection with this name."))


def _save_collection(collection: BookmarkCollection, **kwargs) -> BookmarkCollection:
    try:
        with transaction.atomic():
            collection.save(**kwargs)
    except IntegrityError as error:  # same name created concurrently
        raise _name_taken() from error
    return collection


@transaction.atomic
def create_collection(*, actor, name):
    """A new bookmark collection (at most ``POSTS_MAX_BOOKMARK_COLLECTIONS`` per user)."""
    _ensure_owner(actor, None)
    limit = settings.POSTS_MAX_BOOKMARK_COLLECTIONS
    if BookmarkCollection.objects.filter(user=actor).count() >= limit:
        raise DomainError(
            "too_many_collections",
            _("You already have %(max)s collections: delete one first.") % {"max": limit},
        )
    collection = BookmarkCollection(user=actor, name=_clean_name(actor, name))
    return _save_collection(collection)


@transaction.atomic
def rename_collection(*, actor, collection, name):
    _ensure_owner(actor, collection)
    collection.name = _clean_name(actor, name, exclude_pk=collection.pk)
    return _save_collection(collection, update_fields=["name", "updated_at"])


@transaction.atomic
def delete_collection(*, actor, collection):
    """Delete a collection; its bookmarks stay, outside any collection."""
    _ensure_owner(actor, collection)
    collection.delete()


@transaction.atomic
def move_bookmark(*, actor, bookmark, collection):
    """Move a bookmark into another of the actor's collections (``None``: no collection)."""
    _ensure_owner(actor, bookmark)
    _ensure_owner(actor, collection)
    bookmark.collection = collection
    bookmark.save(update_fields=["collection"])
    return bookmark


# Reports ---------------------------------------------------------------------------


def _target_field(target) -> str:
    return "comment" if isinstance(target, Comment) else "post"


def _open_reports(target):
    return ContentReport.objects.filter(
        **{_target_field(target): target}, status=ContentReport.Status.OPEN
    )


def _after_visibility_change(target) -> None:
    if isinstance(target, Comment):
        schedule_recount(target.post)


def ensure_can_report(*, actor, target) -> None:
    """Raise the refusal ``report`` would answer whatever the form says (``forbidden``,
    ``own_content``, ``invalid_state``, ``already_reported``): the report form is only offered
    when none applies."""
    if not _can_view_target(actor, target):
        raise _forbidden()
    if _is_author(actor, target):
        raise _own_content()
    if not _is_open(target):
        raise _invalid_state()
    if not policies.can_report(actor, target):
        raise _forbidden()
    if _open_reports(target).filter(reporter=actor).exists():
        raise _already_reported()


@transaction.atomic
def report(*, actor, target, reason, details=""):
    """Report a post or comment the actor can read; the ``POSTS_REPORT_AUTOHIDE_THRESHOLD``-th
    distinct open report hides it provisionally (audited ``*.auto_hidden``).

    Moderators+ receive a ``moderation_alert`` for every new report.
    """
    post = _post_of(target)
    if not _can_view_target(actor, target):
        raise _forbidden()
    if _is_author(actor, target):
        raise _own_content()
    if reason not in ContentReport.Reason.values:
        raise DomainError("invalid_reason", _("Choose a reason for your report."))
    details = (details or "").strip()
    if len(details) > REPORT_DETAILS_MAX_LENGTH:
        raise DomainError(
            "body_too_long",
            _("This text is too long (%(max)s characters at most).")
            % {"max": REPORT_DETAILS_MAX_LENGTH},
        )
    # The row lock serialises concurrent reports, so the threshold is crossed exactly once.
    target = _lock(target)
    ensure_can_report(actor=actor, target=target)
    try:
        with transaction.atomic():
            content_report = ContentReport.objects.create(
                reporter=actor,
                community=post.community,
                reason=reason,
                details=details,
                **{_target_field(target): target},
            )
    except IntegrityError as error:
        raise _already_reported() from error
    moderators = _moderators(post.community)
    notify("moderation_alert", moderators, actor=actor, target=target, community=post.community)
    # Reports whose reporter account was deleted no longer count.
    reporters = (
        _open_reports(target).exclude(reporter__isnull=True).values("reporter").distinct().count()
    )
    if reporters >= settings.POSTS_REPORT_AUTOHIDE_THRESHOLD:
        mark_hidden(target, actor=None, reason="", automatic=True)
        if target.author is not None and _can_view_target(target.author, target):
            notify("system", [target.author], target=target, community=post.community)
        _after_visibility_change(target)
    return content_report


def _already_reported():
    return DomainError("already_reported", _("You have already reported this content."))


def _report_target(content_report):
    return content_report.post if content_report.post_id else content_report.comment


def _locked_open_report(actor, content_report) -> ContentReport:
    """Lock the reported target, then the report.

    Every write on the reports of one target (``report`` included) locks the target first, so
    concurrent decisions serialise and each sees the reports the others closed: dismissing
    the last two open reports at once still restores an automatically hidden target.
    """
    target = _report_target(
        ContentReport.objects.select_related("post", "comment").get(pk=content_report.pk)
    )
    _lock(target)
    content_report = ContentReport.objects.select_for_update().get(pk=content_report.pk)
    if content_report.status != ContentReport.Status.OPEN:
        raise _invalid_state()
    target = _report_target(content_report)
    if not (
        policies.can_view_post(actor, _post_of(target))
        and policies.can_moderate(actor, content_report.community)
    ):
        raise _forbidden()
    return content_report


def _close(actor, content_report, status, note: str) -> None:
    content_report.status = status
    content_report.handled_by = actor
    content_report.handled_at = timezone.now()
    content_report.resolution_note = note
    content_report.save(
        update_fields=["status", "handled_by", "handled_at", "resolution_note", "updated_at"]
    )
    action = "report.resolved" if status == ContentReport.Status.RESOLVED else "report.dismissed"
    record(
        actor=actor,
        action=action,
        target=content_report,
        changes={"note": note},
        community=content_report.community,
    )


def _restore_if_cleared(actor, target) -> None:
    """An automatically hidden target comes back once no open report remains."""
    target.refresh_from_db()
    if is_auto_hidden(target) and not _open_reports(target).exists():
        mark_visible(target, actor=actor)
        _after_visibility_change(target)


@transaction.atomic
def resolve_report(*, actor, report, note="", hide=False):
    """Accept a report (audited ``report.resolved``).

    With ``hide`` the target is hidden with ``note`` (required) as the reason and every open
    report of the target is resolved with it; an automatically hidden target becomes hidden by
    the moderator (``confirm_hidden``), a target a moderator already hid stays as it is.
    Without ``hide``, an automatically hidden target is restored once no open report remains.
    """
    content_report = _locked_open_report(actor, report)
    note = (note or "").strip()
    target = _report_target(content_report)
    if hide:
        if is_auto_hidden(target):
            confirm_hidden(target, actor=actor, reason=note)
        elif target.status != target.Status.HIDDEN:
            mark_hidden(target, actor=actor, reason=note)
            _after_visibility_change(target)
        others = _open_reports(target).exclude(pk=content_report.pk).select_for_update()
        for other in [content_report, *others]:
            _close(actor, other, ContentReport.Status.RESOLVED, note)
    else:
        _close(actor, content_report, ContentReport.Status.RESOLVED, note)
        _restore_if_cleared(actor, target)
    report.refresh_from_db()
    return report


@transaction.atomic
def dismiss_report(*, actor, report, note=""):
    """Reject a report (audited ``report.dismissed``); dismissing the last open report of an
    automatically hidden target restores it."""
    content_report = _locked_open_report(actor, report)
    _close(actor, content_report, ContentReport.Status.DISMISSED, (note or "").strip())
    _restore_if_cleared(actor, _report_target(content_report))
    report.refresh_from_db()
    return report
