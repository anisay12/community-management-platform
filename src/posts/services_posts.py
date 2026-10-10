"""Write services on posts (Task 2): creation, editing, review, pinning, hiding, archiving,
accepted answers, sharing and tags.

Every service is atomic, keyword-only with the actor first, checks its policy itself and
raises ``core.errors.DomainError`` (``forbidden``, ``read_only``, ``edit_conflict``, ...).
Notifications and broadcasts go out once the transaction commits.
"""

from django.conf import settings
from django.contrib.postgres.aggregates import StringAgg
from django.contrib.postgres.search import SearchVector
from django.db import connection, transaction
from django.db.models import OuterRef, Subquery, TextField, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext as _

from audit.services import record
from communities.models import Community
from communities.policies import membership_of
from core.errors import DomainError
from core.tasks import delay_on_commit
from notifications.services import notify
from taxonomy.models import Tag
from taxonomy.services import get_or_create_tag, normalize_tag_key

from . import policies, ratelimit
from .hiding import mark_hidden, mark_visible
from .mentions import handles_of, resolve_mentions, sync_mentions
from .models import POST_BODY_MAX_LENGTH, Comment, Post, PostRevision
from .privacy import author_display_for
from .rendering import render_body
from .selectors import community_moderators
from .tasks import broadcast_post

# Kinds whose published edits keep a revision even when the author edits (Decision 13).
REVISED_KINDS = (Post.Kind.ARTICLE, Post.Kind.ANNOUNCEMENT)
# Kinds broadcast in the ``announcement`` category (Decision 16).
ANNOUNCEMENT_KINDS = (Post.Kind.ARTICLE, Post.Kind.ANNOUNCEMENT)


# --- errors -----------------------------------------------------------------------------


def _forbidden():
    return DomainError("forbidden", _("You are not allowed to perform this action."))


def _invalid_state():
    return DomainError("invalid_state", _("This action is not possible in the current state."))


def _ensure_live(community) -> None:
    if community.is_read_only:
        raise DomainError(
            "read_only", _("This community is suspended or archived: it cannot be changed.")
        )


# --- helpers ----------------------------------------------------------------------------


def _lock(post: Post) -> Post:
    """Re-read ``post`` under a row lock (in place) so concurrent writes serialise."""
    post.refresh_from_db(from_queryset=Post.objects.select_for_update())
    return post


def _clean_content(title: str, body: str) -> tuple[str, str]:
    title = (title or "").strip()
    body = body or ""
    if not title:
        raise DomainError("title_required", _("Give the post a title."))
    if len(title) > Post._meta.get_field("title").max_length:
        raise DomainError("title_too_long", _("The title is too long."))
    if len(body) > POST_BODY_MAX_LENGTH:
        raise DomainError(
            "body_too_long",
            _("The text is too long (at most %(limit)s characters).")
            % {"limit": POST_BODY_MAX_LENGTH},
        )
    return title, body


def _edit_conflict():
    return DomainError(
        "edit_conflict", _("This post was changed by someone else since you opened it.")
    )


def _ensure_version(post: Post, version) -> None:
    """``version`` (as sent by the editor, possibly garbage) must be the current one."""
    try:
        expected = int(version)
    except (TypeError, ValueError):
        expected = None
    if expected != post.version:
        raise _edit_conflict()


def _resolve_tags(actor, community, names) -> list[Tag]:
    """Tags for ``names`` (strings or ``Tag`` instances), matched by key.

    Unknown names are created only when the actor may create tags in ``community``.
    """
    found: list[Tag] = []
    seen: set[str] = set()
    max_length = Tag._meta.get_field("name").max_length
    for name in names or ():
        if isinstance(name, Tag):
            if name.key not in seen:
                seen.add(name.key)
                found.append(name)
            continue
        name = " ".join(str(name).split())[:max_length].strip()
        key = normalize_tag_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        tag = Tag.objects.filter(key=key).first()
        if tag is None:
            if not policies.can_create_tag(actor, community):
                raise DomainError(
                    "tag_creation_forbidden",
                    _("You can only choose existing tags: “%(name)s” does not exist.")
                    % {"name": name},
                )
            tag = get_or_create_tag(name)
        found.append(tag)
    _hold_tags(found)
    return found


def _hold_tags(tags) -> None:
    """Take on ``tags`` the lock a foreign key check takes (``FOR KEY SHARE``) until the end of
    the transaction, so a tag merged away meanwhile answers ``tag_unavailable`` instead of a
    foreign key error. ``taxonomy.services.merge_tags`` locks its tags ``FOR UPDATE``: it
    either waits for this transaction or has deleted the tag before."""
    ids = sorted({tag.pk for tag in tags})
    if not ids:
        return
    # Django has no ``FOR KEY SHARE`` (``select_for_update`` would block the tag's readers).
    table = connection.ops.quote_name(Tag._meta.db_table)  # a model constant, not user input
    sql = f"SELECT id FROM {table} WHERE id = ANY(%s) ORDER BY id FOR KEY SHARE"  # noqa: S608  # nosec B608
    with connection.cursor() as cursor:
        cursor.execute(sql, [ids])
        held = {row[0] for row in cursor.fetchall()}
    if held != set(ids):
        raise DomainError(
            "tag_unavailable",
            _("A tag you chose has just been merged or deleted: check the tags and save again."),
        )


def _search_vector() -> SearchVector:
    """Title (A), tag names (B) and body (C) of the post updated, ``simple`` configuration."""
    tag_names = (
        Post.tags.through.objects.filter(post_id=OuterRef("pk"))
        .order_by()
        .values("post_id")
        .annotate(names=StringAgg("tag__name", delimiter=" ", order_by="tag__name"))
        .values("names")
    )
    return (
        SearchVector("title", weight="A", config="simple")
        + SearchVector(
            Coalesce(
                Subquery(tag_names, output_field=TextField()),
                Value("", output_field=TextField()),
            ),
            weight="B",
            config="simple",
        )
        + SearchVector("body", weight="C", config="simple")
    )


def refresh_search_vectors(posts) -> int:
    """Recompute the search vector of every post of the queryset ``posts`` in one UPDATE."""
    return posts.update(search_vector=_search_vector())


def refresh_search_vector(post: Post) -> None:
    """Recompute the search vector of ``post`` (after a change of title, body or tags)."""
    refresh_search_vectors(Post.objects.filter(pk=post.pk))


def _needs_review(actor, community) -> bool:
    return community.require_post_review and not policies.is_content_moderator(actor, community)


def _notify_readers(category, post: Post, users, *, actor, target=None) -> None:
    """Notify those of ``users`` who can open ``post`` (a former member of a private
    community hears nothing about it)."""
    readers = [user for user in users if user is not None and policies.can_view_post(user, post)]
    if readers:
        notify(category, readers, actor=actor, target=target or post, community=post.community)


def _mentioned_users(post: Post) -> list:
    """Members mentioned in ``post``, resolved now; for a published post, only its readers."""
    users = resolve_mentions(post.community, post.body)
    if post.status == Post.Status.PUBLISHED:
        users = [user for user in users if policies.can_view_post(user, post)]
    return users


def _render(post: Post) -> list:
    """Render ``post.body`` into ``post.body_html`` (not saved), with spans on the mentions
    that resolve now; returns the mentioned users (see ``_mentioned_users``)."""
    mentioned = _mentioned_users(post)
    post.body_html = render_body(post.body, handles_of(mentioned))
    return mentioned


def _sync_post_mentions(post: Post, actor, mentioned) -> list:
    """Store the mentions of ``post``; notify the newly mentioned users if it is published."""
    new_users = sync_mentions(post, mentioned)
    if post.status == Post.Status.PUBLISHED:
        _notify_readers("mention", post, new_users, actor=actor)
    return new_users


def _go_live(post: Post) -> None:
    """Publish ``post`` (already locked): status, dates, community activity, broadcast and
    notification of everyone it mentions."""
    now = timezone.now()
    post.status = Post.Status.PUBLISHED
    post.published_at = now
    post.last_activity_at = now
    post.review_note = ""
    # Mentions stored with the draft may be stale (members left, others joined): resolve them
    # again, and render the body again to match. The author mentions, even when a moderator
    # approves the post.
    mentioned = _render(post)
    post.save(
        update_fields=[
            "status",
            "published_at",
            "last_activity_at",
            "review_note",
            "body_html",
            "updated_at",
        ]
    )
    Community.objects.filter(pk=post.community_id).update(last_activity_at=now)
    category = "announcement" if post.kind in ANNOUNCEMENT_KINDS else "community_post"
    delay_on_commit(broadcast_post, post.pk, category)
    sync_mentions(post, mentioned)
    _notify_readers("mention", post, mentioned, actor=post.author)


def _submit(post: Post, actor) -> None:
    """Publish ``post`` or send it to review, depending on the community and the actor."""
    if _needs_review(actor, post.community):
        post.status = Post.Status.PENDING_REVIEW
        post.review_note = ""
        post.save(update_fields=["status", "review_note", "updated_at"])
        notify(
            "review_request",
            community_moderators(post.community),
            actor=actor,
            target=post,
            community=post.community,
        )
    else:
        _go_live(post)


def _ensure_can_create(actor, community, kind) -> None:
    _ensure_live(community)
    if policies.can_create_post(actor, community, kind):
        return
    if membership_of(actor, community) is None:
        raise DomainError("not_member", _("You are not a member of this community."))
    raise DomainError("kind_forbidden", _("You cannot publish this kind of post here."))


def _create(*, actor, community, kind, title, body, tags, publish, shared_from=None) -> Post:
    title, body = _clean_content(title, body)
    tag_objects = _resolve_tags(actor, community, tags)
    mentioned = resolve_mentions(community, body)
    # Counted once every check has passed, right before the write: a refusal costs nothing.
    ratelimit.hit(actor, "post")
    post = Post.objects.create(
        community=community,
        author=actor,
        author_display=author_display_for(actor),
        kind=kind,
        title=title,
        body=body,
        body_html=render_body(body, handles_of(mentioned)),
        status=Post.Status.DRAFT,
        shared_from=shared_from,
    )
    post.tags.set(tag_objects)
    refresh_search_vector(post)
    sync_mentions(post, mentioned)
    if publish:
        _submit(post, actor)
    return post


# --- creation and drafts ----------------------------------------------------------------


@transaction.atomic
def create_post(*, actor, community, kind, title, body, tags=(), publish=True) -> Post:
    """Create a post: a draft (``publish=False``), a post pending review (community requires
    review and the actor is below moderator) or a published post."""
    _ensure_can_create(actor, community, kind)
    return _create(
        actor=actor,
        community=community,
        kind=kind,
        title=title,
        body=body,
        tags=tags,
        publish=publish,
    )


@transaction.atomic
def publish_draft(*, actor, post) -> Post:
    """Publish (or submit for review) the actor's own draft."""
    _lock(post)
    _ensure_live(post.community)
    if post.author_id is None or post.author_id != getattr(actor, "pk", None):
        raise _forbidden()
    if post.status != Post.Status.DRAFT:
        raise _invalid_state()
    _ensure_can_create(actor, post.community, post.kind)
    _submit(post, actor)
    return post


@transaction.atomic
def delete_draft(*, actor, post) -> None:
    """Delete the actor's own draft (published content is hidden or archived instead)."""
    _ensure_live(post.community)
    if not policies.can_delete_draft(actor, post):
        raise _forbidden()
    _lock(post)
    if post.status != Post.Status.DRAFT:
        raise _invalid_state()
    post.delete()


# --- editing ----------------------------------------------------------------------------


@transaction.atomic
def update_post(*, actor, post, title, body, tags, version) -> Post:
    """Edit title, body and (unless ``tags`` is ``None``) tags of ``post``.

    ``version`` is the one the editor started from: a mismatch raises ``edit_conflict``. A
    revision keeps the previous text for published articles and announcements and for any
    edit by someone other than the author (none when title and body are unchanged); a
    moderator's edit is audited and notified.
    """
    _lock(post)
    _ensure_live(post.community)
    if not policies.can_edit_post(actor, post):
        raise _forbidden()
    _ensure_version(post, version)
    title, body = _clean_content(title, body)
    tag_objects = None if tags is None else _resolve_tags(actor, post.community, tags)
    by_other = post.author_id != actor.pk
    text_changed = (title, body) != (post.title, post.body)
    if text_changed and (
        by_other or (post.status == Post.Status.PUBLISHED and post.kind in REVISED_KINDS)
    ):
        PostRevision.objects.create(post=post, editor=actor, title=post.title, body=post.body)
    previous_title = post.title
    post.title = title
    post.body = body
    mentioned = _render(post)
    post.version += 1
    post.save(update_fields=["title", "body", "body_html", "version", "updated_at"])
    if tag_objects is not None:
        post.tags.set(tag_objects)
    refresh_search_vector(post)
    _sync_post_mentions(post, actor, mentioned)
    if by_other:
        record(
            actor=actor,
            action="post.edited_by_moderator",
            target=post,
            changes={"title": {"before": previous_title, "after": title}},
            community=post.community,
        )
        _notify_readers("system", post, [post.author], actor=actor)
    return post


@transaction.atomic
def set_tags(*, actor, post, names, version=None) -> Post:
    """Replace the tags of ``post``; members below contributor only pick existing tags.

    Changing tags is an edit: it bumps ``version``, and ``version`` (when the caller started
    from a known one) must still be current (``edit_conflict``).
    """
    _lock(post)
    _ensure_live(post.community)
    if not policies.can_edit_post(actor, post):
        raise _forbidden()
    if version is not None:
        _ensure_version(post, version)
    post.tags.set(_resolve_tags(actor, post.community, names))
    post.version += 1
    post.save(update_fields=["version", "updated_at"])
    refresh_search_vector(post)
    return post


# --- review -----------------------------------------------------------------------------


def _ensure_pending_review(actor, post) -> None:
    if not policies.can_review(actor, post.community):
        raise _forbidden()
    if post.status != Post.Status.PENDING_REVIEW:
        raise _invalid_state()


@transaction.atomic
def approve_review(*, actor, post) -> Post:
    """Publish a pending post (moderator+), broadcast it and tell its author."""
    _lock(post)
    _ensure_pending_review(actor, post)
    _ensure_live(post.community)
    _go_live(post)
    record(actor=actor, action="post.review_approved", target=post, community=post.community)
    _notify_readers("system", post, [post.author], actor=actor)
    return post


@transaction.atomic
def reject_review(*, actor, post, note) -> Post:
    """Send a pending post back to draft with ``note`` (mandatory) for its author."""
    _lock(post)
    _ensure_pending_review(actor, post)
    note = (note or "").strip()
    if not note:
        raise DomainError("reason_required", _("Explain to the author why the post is refused."))
    post.status = Post.Status.DRAFT
    post.review_note = note
    post.save(update_fields=["status", "review_note", "updated_at"])
    record(
        actor=actor,
        action="post.review_rejected",
        target=post,
        changes={"note": note},
        community=post.community,
    )
    _notify_readers("system", post, [post.author], actor=actor)
    return post


# --- pinning ----------------------------------------------------------------------------


@transaction.atomic
def pin_post(*, actor, post) -> Post:
    """Pin a published post (facilitator+); at most ``POSTS_PIN_LIMIT`` per community."""
    _ensure_live(post.community)
    if not policies.can_pin(actor, post.community):
        raise _forbidden()
    # Lock order everywhere: the post, then its community (publishing and commenting update
    # the community's activity after writing the post). Concurrent pins in one community
    # serialise on the community lock, taken before counting.
    _lock(post)
    Community.objects.select_for_update().filter(pk=post.community_id).first()
    if post.status != Post.Status.PUBLISHED or post.pinned_at is not None:
        raise _invalid_state()
    pinned = Post.objects.filter(
        community_id=post.community_id, pinned_at__isnull=False, status=Post.Status.PUBLISHED
    ).count()
    if pinned >= settings.POSTS_PIN_LIMIT:
        raise DomainError(
            "pin_limit",
            _("At most %(limit)s posts can be pinned: unpin one first.")
            % {"limit": settings.POSTS_PIN_LIMIT},
        )
    post.pinned_at = timezone.now()
    post.pinned_by = actor
    post.save(update_fields=["pinned_at", "pinned_by", "updated_at"])
    record(actor=actor, action="post.pinned", target=post, community=post.community)
    return post


def _clear_pin(post: Post) -> None:
    if post.pinned_at is not None:
        post.pinned_at = None
        post.pinned_by = None
        post.save(update_fields=["pinned_at", "pinned_by", "updated_at"])


@transaction.atomic
def unpin_post(*, actor, post) -> Post:
    _ensure_live(post.community)
    if not policies.can_pin(actor, post.community):
        raise _forbidden()
    _lock(post)
    if post.pinned_at is None:
        raise _invalid_state()
    _clear_pin(post)
    record(actor=actor, action="post.unpinned", target=post, community=post.community)
    return post


# --- hiding and archiving ---------------------------------------------------------------


@transaction.atomic
def hide_post(*, actor, post, reason) -> Post:
    """Hide a post (moderator+, reason required); its author still reads it, with the reason.

    Works in suspended and archived communities. A hidden post loses its pin (``mark_hidden``).
    """
    if not policies.can_moderate(actor, post.community):
        raise _forbidden()
    _lock(post)
    if post.status == Post.Status.DRAFT:
        raise _invalid_state()
    mark_hidden(post, actor=actor, reason=reason)
    _notify_readers("system", post, [post.author], actor=actor)
    return post


@transaction.atomic
def unhide_post(*, actor, post) -> Post:
    """Restore a hidden post to its previous status (moderator+)."""
    if not policies.can_moderate(actor, post.community):
        raise _forbidden()
    mark_visible(post, actor=actor)
    _notify_readers("system", post, [post.author], actor=actor)
    return post


@transaction.atomic
def archive_post(*, actor, post) -> Post:
    """Archive a published post (moderator+): read-only, readable by link, out of feeds."""
    if not policies.can_moderate(actor, post.community):
        raise _forbidden()
    _lock(post)
    if post.status != Post.Status.PUBLISHED:
        raise _invalid_state()
    post.status = Post.Status.ARCHIVED
    post.archived_at = timezone.now()
    post.pinned_at = None
    post.pinned_by = None
    post.save(update_fields=["status", "archived_at", "pinned_at", "pinned_by", "updated_at"])
    record(actor=actor, action="post.archived", target=post, community=post.community)
    return post


# --- accepted answers -------------------------------------------------------------------


def _ensure_answerable(actor, post) -> None:
    """Rights first (``forbidden`` reveals nothing about the post), then the state."""
    _lock(post)
    _ensure_live(post.community)
    if not policies.may_decide_answers(actor, post):
        raise _forbidden()
    if not policies.can_accept_answer(actor, post):
        raise _invalid_state()  # not a question, or not published


@transaction.atomic
def accept_answer(*, actor, post, comment) -> Post:
    """Mark ``comment`` (a visible top-level comment of ``post``) as the accepted answer
    (question author, expert+ or moderator+)."""
    _ensure_answerable(actor, post)
    # Re-read the comment under a lock (after the post): it may have been hidden meanwhile.
    comment.refresh_from_db(from_queryset=Comment.objects.select_for_update())
    if (
        comment.post_id != post.pk
        or comment.parent_id is not None
        or comment.status != Comment.Status.VISIBLE
    ):
        raise _invalid_state()
    post.accepted_answer = comment
    post.save(update_fields=["accepted_answer", "updated_at"])
    _notify_readers("system", post, [comment.author], actor=actor)
    return post


@transaction.atomic
def clear_accepted_answer(*, actor, post) -> Post:
    _ensure_answerable(actor, post)
    if post.accepted_answer_id is not None:
        post.accepted_answer = None
        post.save(update_fields=["accepted_answer", "updated_at"])
    return post


# --- sharing ----------------------------------------------------------------------------


@transaction.atomic
def share_post(*, actor, post, target_community, title, comment="") -> Post:
    """Publish a ``discussion`` in ``target_community`` pointing to ``post`` (``shared_from``),
    titled ``title`` (required, chosen by the actor; the editor may prefill it with the
    original's title) with ``comment`` as its body. Nothing of the original (title, body,
    tags) is copied into the new post or its search vector: the original may be private.
    The target's review rule applies as for any new post."""
    _ensure_live(target_community)
    if not policies.can_share(actor, post, target_community):
        if (
            policies.can_view_post(actor, post)
            and post.status == Post.Status.PUBLISHED
            and target_community.pk != post.community_id
            and membership_of(actor, target_community) is None
        ):
            raise DomainError("not_member", _("You are not a member of this community."))
        raise _forbidden()
    return _create(
        actor=actor,
        community=target_community,
        kind=Post.Kind.DISCUSSION,
        title=title,
        body=comment,
        tags=(),
        publish=True,
        shared_from=post,
    )
