"""Shared low-level hiding of posts and comments (status change and audit event).

The services of moderation (``services_posts``), reports and comments
(``services_interactions``) call these after checking their own policies.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from audit.services import record
from core.errors import DomainError

from .models import Comment, Post

AUTOMATIC_REASON = "automatic"


def _prefix(target) -> str:
    return "comment" if isinstance(target, Comment) else "post"


def _lock(target) -> None:
    """Re-read ``target`` under a row lock, so concurrent hide/unhide calls serialise and the
    state checks below see the committed status (never a stale in-memory copy)."""
    target.refresh_from_db(from_queryset=type(target).objects.select_for_update())


def _community(target):
    return target.post.community if isinstance(target, Comment) else target.community


@transaction.atomic
def mark_hidden(target, *, actor, reason, automatic=False):
    """Hide ``target`` (a post or comment) and record ``<kind>.hidden`` or ``.auto_hidden``.

    A post remembers its status in ``status_before_hidden`` so ``mark_visible`` restores it,
    and loses its pin (a restored post never exceeds the community's pin limit). Automatic
    hiding (report threshold) has no ``hidden_by`` and the reason ``"automatic"``.
    """
    reason = AUTOMATIC_REASON if automatic else (reason or "").strip()
    if not reason:
        raise DomainError("reason_required", _("Give a reason for hiding this content."))
    _lock(target)
    if target.status == target.Status.HIDDEN:
        raise DomainError("invalid_state", _("This action is not possible in the current state."))
    fields = ["status", "hidden_at", "hidden_by", "hidden_reason", "updated_at"]
    if isinstance(target, Post):
        target.status_before_hidden = target.status
        target.pinned_at = None
        target.pinned_by = None
        fields.extend(["status_before_hidden", "pinned_at", "pinned_by"])
    target.status = target.Status.HIDDEN
    target.hidden_at = timezone.now()
    target.hidden_by = None if automatic else actor
    target.hidden_reason = reason
    target.save(update_fields=fields)
    action = f"{_prefix(target)}.{'auto_hidden' if automatic else 'hidden'}"
    record(
        actor=None if automatic else actor,
        action=action,
        target=target,
        changes={"reason": reason},
        community=_community(target),
    )
    return target


@transaction.atomic
def mark_visible(target, *, actor=None):
    """Restore a hidden ``target`` (posts get back ``status_before_hidden``, by default
    published) and record ``<kind>.unhidden`` with ``actor`` (``None`` for the system)."""
    _lock(target)
    if target.status != target.Status.HIDDEN:
        raise DomainError("invalid_state", _("This action is not possible in the current state."))
    fields = ["status", "hidden_at", "hidden_by", "hidden_reason", "updated_at"]
    if isinstance(target, Post):
        target.status = target.status_before_hidden or Post.Status.PUBLISHED
        target.status_before_hidden = ""
        fields.append("status_before_hidden")
    else:
        target.status = Comment.Status.VISIBLE
    previous_reason = target.hidden_reason
    target.hidden_at = None
    target.hidden_by = None
    target.hidden_reason = ""
    target.save(update_fields=fields)
    record(
        actor=actor,
        action=f"{_prefix(target)}.unhidden",
        target=target,
        changes={"reason": previous_reason},
        community=_community(target),
    )
    return target


def is_auto_hidden(target) -> bool:
    """Hidden by the report threshold (no moderator decision yet)."""
    return (
        target.status == target.Status.HIDDEN
        and target.hidden_by_id is None
        and target.hidden_reason == AUTOMATIC_REASON
    )


@transaction.atomic
def confirm_hidden(target, *, actor, reason):
    """Turn an automatic hiding into a moderator's one: ``hidden_by`` and ``reason`` replace
    the provisional values (so dismissing reports no longer restores the target) and
    ``<kind>.hidden`` is recorded with the reason."""
    reason = (reason or "").strip()
    if not reason:
        raise DomainError("reason_required", _("Give a reason for hiding this content."))
    _lock(target)
    if not is_auto_hidden(target):
        raise DomainError("invalid_state", _("This action is not possible in the current state."))
    target.hidden_at = timezone.now()
    target.hidden_by = actor
    target.hidden_reason = reason
    target.save(update_fields=["hidden_at", "hidden_by", "hidden_reason", "updated_at"])
    record(
        actor=actor,
        action=f"{_prefix(target)}.hidden",
        target=target,
        changes={"reason": reason},
        community=_community(target),
    )
    return target
