"""Authorization rules for posts and interactions: pure predicates that never raise.

Reading: a post the user may not see (``can_view_post`` false) answers 404; a visible post
where the action is refused answers 403. Writing (publishing, commenting, reacting, editing
one's own content) needs a current membership of an ``active`` community; moderating needs the
community moderator role or above, or a functional administrator who can read the content.
Moderation (reports, hiding and unhiding, reviewing pending posts) works whatever the community
status (Decision 9 clarification); pinning, tag creation and editing still need an ``active``
community. Reporting and bookmarking need read access only.
"""

from communities.models import Community
from communities.policies import (
    can_view_content,
    is_functional_admin,
    membership_of,
)
from communities.roles import CommunityRole, role_at_least

from .models import Comment, Post

READABLE_STATUSES = (Post.Status.PUBLISHED, Post.Status.ARCHIVED)


def _is_active_user(user) -> bool:
    return bool(getattr(user, "is_authenticated", False) and user.is_active)


def _has_role(user, community, minimum) -> bool:
    membership = membership_of(user, community)
    return membership is not None and role_at_least(membership.role, minimum)


def _is_live(community) -> bool:
    return community.status == Community.Status.ACTIVE


def _is_author(user, content) -> bool:
    return content.author_id is not None and content.author_id == getattr(user, "pk", None)


def _admin_with_content(user, community) -> bool:
    return is_functional_admin(user) and can_view_content(user, community)


def is_content_moderator(user, community) -> bool:
    """Moderator+ of ``community``, or a functional admin who can read its content.

    Ignores the community status: it decides who *sees* pending and hidden content.
    """
    if not _is_active_user(user):
        return False
    return _has_role(user, community, CommunityRole.MODERATOR) or _admin_with_content(
        user, community
    )


def _can_write(user, community) -> bool:
    """A current member of an active community."""
    return (
        _is_active_user(user) and _is_live(community) and membership_of(user, community) is not None
    )


# Reading ---------------------------------------------------------------------------


def can_view_post(user, post) -> bool:
    if not _is_active_user(user):
        return False
    if post.status in READABLE_STATUSES:
        return can_view_content(user, post.community)
    if _is_author(user, post):
        return True
    if post.status == Post.Status.DRAFT:
        return False
    return is_content_moderator(user, post.community)


def can_view_revisions(user, post) -> bool:
    return can_view_post(user, post) and is_content_moderator(user, post.community)


def can_view_comment(user, comment) -> bool:
    if not can_view_post(user, comment.post):
        return False
    if comment.status == Comment.Status.VISIBLE:
        return True
    return _is_author(user, comment) or is_content_moderator(user, comment.post.community)


# Posts -----------------------------------------------------------------------------


def can_create_post(user, community, kind) -> bool:
    if not _is_active_user(user) or not _is_live(community):
        return False
    if kind == Post.Kind.ANNOUNCEMENT:
        return _has_role(user, community, CommunityRole.ANIMATOR) or _admin_with_content(
            user, community
        )
    if kind == Post.Kind.ARTICLE:
        return _has_role(user, community, CommunityRole.CONTRIBUTOR)
    if kind in (Post.Kind.DISCUSSION, Post.Kind.QUESTION):
        return membership_of(user, community) is not None
    return False


def can_moderate(user, community) -> bool:
    """Resolve or dismiss reports, hide and unhide: any community status (Decision 9)."""
    return is_content_moderator(user, community)


def can_pin(user, community) -> bool:
    if not _is_active_user(user) or not _is_live(community):
        return False
    return _has_role(user, community, CommunityRole.ANIMATOR) or _admin_with_content(
        user, community
    )


def can_review(user, community) -> bool:
    """Decide on pending posts: any community status (Decision 9)."""
    return is_content_moderator(user, community)


def _can_moderate_live(user, community) -> bool:
    """Moderator rights that write new content (editing, accepting): active communities only."""
    return _is_live(community) and is_content_moderator(user, community)


def can_edit_post(user, post) -> bool:
    if post.status == Post.Status.ARCHIVED or not can_view_post(user, post):
        return False
    if _can_moderate_live(user, post.community):
        return True
    return (
        _is_author(user, post)
        and post.status != Post.Status.HIDDEN
        and _can_write(user, post.community)
    )


def can_delete_draft(user, post) -> bool:
    """The author of a draft, while its community is active (read-only otherwise)."""
    return (
        _is_active_user(user)
        and post.status == Post.Status.DRAFT
        and _is_author(user, post)
        and _is_live(post.community)
    )


def can_accept_answer(user, post) -> bool:
    if post.kind != Post.Kind.QUESTION or post.status != Post.Status.PUBLISHED:
        return False
    if not can_view_post(user, post):
        return False
    return _can_moderate_live(user, post.community) or (
        _is_author(user, post) and _can_write(user, post.community)
    )


def can_share(user, post, target_community) -> bool:
    if post.status != Post.Status.PUBLISHED or target_community.pk == post.community_id:
        return False
    return can_view_post(user, post) and can_create_post(
        user, target_community, Post.Kind.DISCUSSION
    )


# Comments and interactions ---------------------------------------------------------------


def can_comment(user, post) -> bool:
    return (
        post.status == Post.Status.PUBLISHED
        and can_view_post(user, post)
        and _can_write(user, post.community)
    )


def can_edit_comment(user, comment) -> bool:
    return (
        comment.status == Comment.Status.VISIBLE
        and _is_author(user, comment)
        and can_comment(user, comment.post)
    )


def _is_open_target(target) -> bool:
    if isinstance(target, Comment):
        return (
            target.status == Comment.Status.VISIBLE and target.post.status == Post.Status.PUBLISHED
        )
    return target.status == Post.Status.PUBLISHED


def _post_of(target):
    return target.post if isinstance(target, Comment) else target


def can_react(user, target) -> bool:
    post = _post_of(target)
    return (
        _is_open_target(target)
        and not _is_author(user, target)
        and can_view_post(user, post)
        and _can_write(user, post.community)
    )


def can_bookmark(user, post) -> bool:
    return post.status in READABLE_STATUSES and can_view_post(user, post)


def can_report(user, target) -> bool:
    return (
        _is_open_target(target)
        and not _is_author(user, target)
        and can_view_post(user, _post_of(target))
    )


# Tags ------------------------------------------------------------------------------


def can_create_tag(user, community) -> bool:
    if not _is_active_user(user) or not _is_live(community):
        return False
    return _has_role(user, community, CommunityRole.CONTRIBUTOR) or is_functional_admin(user)


def can_merge_tags(user) -> bool:
    return is_functional_admin(user)
