"""Template helpers of the interaction partials (reaction bars, comment actions, bookmarks).

The permissions of the viewer on a post page are computed once per post (``post_interactions``,
memoised on the post instance) and derived per comment without further queries, so the
partials included for every comment keep the detail page within its query ceiling. The
services re-check every rule on submit.
"""

from dataclasses import dataclass, field

from django import template
from django.db.models import Q

from communities.models import Community
from communities.policies import can_join, membership_of
from posts import policies
from posts.models import Bookmark, Comment, Post, Reaction

register = template.Library()

STATE_ATTR = "_interaction_state"


@dataclass
class InteractionState:
    """What the viewer may do on one post page."""

    user_pk: int | None
    is_member: bool = False
    can_participate: bool = False  # comment, reply, react (published post, active membership)
    is_moderator: bool = False
    can_react_post: bool = False
    can_report_post: bool = False
    can_report_comments: bool = False  # a reader of a published post
    can_bookmark: bool = False
    bookmarked: bool = False
    join_prompt: bool = False  # a reader who must join to take part
    can_join: bool = False
    asks_to_join: bool = False  # joining needs a facilitator's approval
    post_reactions: set = field(default_factory=set)
    comment_reactions: dict = field(default_factory=dict)


def interaction_state(user, post, *, moderator=None) -> InteractionState:
    """The ``InteractionState`` of ``user`` on ``post`` (memoised on ``post``).

    ``moderator`` is whether ``user`` moderates the post's community, when already known.
    """
    state = getattr(post, STATE_ATTR, None)
    if state is not None and state.user_pk == getattr(user, "pk", None):
        return state
    state = InteractionState(user_pk=getattr(user, "pk", None))
    if policies.can_view_post(user, post):
        community = post.community
        state.is_member = membership_of(user, community) is not None
        state.can_participate = policies.can_comment(user, post)
        if moderator is None:
            moderator = policies.is_content_moderator(user, community)
        state.is_moderator = moderator
        state.can_react_post = policies.can_react(user, post)
        state.can_report_post = policies.can_report(user, post)
        state.can_report_comments = post.status == Post.Status.PUBLISHED
        state.can_bookmark = policies.can_bookmark(user, post)
        state.bookmarked = Bookmark.objects.filter(user=user, post=post).exists()
        live = community.status == Community.Status.ACTIVE
        state.join_prompt = not state.is_member and live and post.status == Post.Status.PUBLISHED
        state.can_join = can_join(user, community)
        state.asks_to_join = community.access_mode == Community.AccessMode.REQUEST
        for post_id, comment_id, kind in Reaction.objects.filter(
            Q(post=post) | Q(comment__post=post), user=user
        ).values_list("post_id", "comment_id", "kind"):
            if post_id:
                state.post_reactions.add(kind)
            else:
                state.comment_reactions.setdefault(comment_id, set()).add(kind)
    setattr(post, STATE_ATTR, state)
    return state


@register.simple_tag(takes_context=True)
def post_interactions(context, post) -> InteractionState:
    return interaction_state(context["request"].user, post, moderator=context.get("is_moderator"))


@dataclass
class CommentState:
    can_reply: bool
    can_edit: bool
    can_react: bool
    can_report: bool
    can_hide: bool
    can_unhide: bool
    reactions: set
    top_public_id: object  # the top-level comment, re-rendered by the HTMX actions


@register.simple_tag
def comment_interactions(state: InteractionState, comment) -> CommentState:
    """Per-comment rights derived from the post's ``state`` (mirrors ``posts.policies``)."""
    visible = comment.status == Comment.Status.VISIBLE
    own = comment.author_id is not None and comment.author_id == state.user_pk
    # ``parent`` is cached by the thread's prefetch (and by the views re-rendering a comment).
    top = comment.parent if comment.parent_id else comment
    return CommentState(
        can_reply=visible and state.can_participate,
        can_edit=visible and own and state.can_participate,
        can_react=visible and not own and state.can_participate,
        can_report=visible and not own and state.can_report_comments,
        can_hide=visible and state.is_moderator,
        can_unhide=not visible and state.is_moderator,
        reactions=state.comment_reactions.get(comment.pk, set()),
        top_public_id=top.public_id,
    )


@register.simple_tag
def reaction_buttons(counts, mine) -> list[tuple[str, str, int, bool]]:
    """``(kind, label, count, pressed)`` of the three reaction kinds, in kind order."""
    counts = counts or {}
    return [
        (kind, str(label), _count(counts.get(kind)), kind in (mine or ()))
        for kind, label in Reaction.Kind.choices
    ]


@register.filter
def has_reactions(items) -> bool:
    """Whether any of the ``reaction_buttons`` items has a non-zero count."""
    return any(count for _kind, _label, count, _pressed in items)


def _count(value) -> int:
    return value if isinstance(value, int) and value > 0 else 0
