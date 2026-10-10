"""Which editor and moderation actions a user is offered on a post (Task 5).

The buttons only mirror the policies; the services check them again on submission.
"""

from django import template
from django.utils.translation import gettext as _

from communities.models import Community
from communities.policies import prime_membership_cache

from .. import policies
from ..models import Post

register = template.Library()

S = Post.Status


def share_targets(user, post) -> list:
    """Active communities other than the post's where ``user`` may share ``post``."""
    if post.status != S.PUBLISHED:
        return []
    communities = list(
        Community.objects.filter(memberships__user=user, status=Community.Status.ACTIVE)
        .exclude(pk=post.community_id)
        .order_by("name")
    )
    prime_membership_cache(user, communities)
    return [community for community in communities if policies.can_share(user, post, community)]


@register.simple_tag
def creatable_kinds(user, community) -> list[str]:
    """The post kinds ``user`` may publish in ``community`` (empty: no "New post" button)."""
    return [kind for kind in Post.Kind.values if policies.can_create_post(user, community, kind)]


@register.simple_tag
def post_actions(user, post, moderator=None) -> dict:
    """The actions offered to ``user`` on ``post``. ``moderator`` passes an already computed
    ``policies.is_content_moderator`` (the detail view has it); policies that only a
    moderator or the author can satisfy are not evaluated for anyone else."""
    community = post.community
    if moderator is None or moderator == "":
        moderator = policies.is_content_moderator(user, community)
    status = post.status
    is_author = post.author_id is not None and post.author_id == user.pk
    can_pin = status == S.PUBLISHED and policies.can_pin(user, community)
    accept = policies.can_accept_answer(user, post)
    publish = is_author and status == S.DRAFT and not community.is_read_only
    review = moderator and status == S.PENDING_REVIEW and policies.can_review(user, community)
    return {
        "edit": (is_author or moderator) and policies.can_edit_post(user, post),
        "publish": publish,
        "publish_label": publish_label(user, community) if publish else "",
        "delete_draft": policies.can_delete_draft(user, post),
        "review": review,
        # Sending back works in any community status; approving publishes, so active only.
        "approve": review and not community.is_read_only,
        "pin": can_pin and post.pinned_at is None,
        "unpin": can_pin and post.pinned_at is not None,
        "hide": moderator and status in (S.PUBLISHED, S.PENDING_REVIEW, S.ARCHIVED),
        "unhide": moderator and status == S.HIDDEN,
        "archive": moderator and status == S.PUBLISHED,
        "share": status == S.PUBLISHED and bool(share_targets(user, post)),
        # Accepting is a button on each eligible comment (``_comment_actions.html``).
        "clear_answer": accept and post.accepted_answer_id is not None,
    }


def publish_label(user, community) -> str:
    if community.require_post_review and not policies.is_content_moderator(user, community):
        return _("Submit for review")
    return _("Publish")
