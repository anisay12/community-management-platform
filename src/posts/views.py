"""Read side of posts: community feed, home feed, post detail and revisions.

A post the viewer may not open answers 404 (also under a wrong community slug); a visible
post whose page is refused answers 403. The community feed answers 404 to non-members of a
private community, and 403 where the community content is closed to a viewer who knows it
exists (technical administrator or auditor on an open community, functional administrator
without a valid grant), like the Members tab.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_safe

from communities.models import Community
from communities.policies import is_functional_admin
from communities.views import community_page

from . import policies, selectors
from .models import Post
from .rendering import render_body

UNANSWERED = "unanswered=1"
# (label, query string) of the feed filters, in display order.
FEED_FILTERS = (
    (_("All"), ""),
    (_("Discussions"), f"kind={Post.Kind.DISCUSSION}"),
    (_("Questions"), f"kind={Post.Kind.QUESTION}"),
    (_("Announcements"), f"kind={Post.Kind.ANNOUNCEMENT}"),
    (_("Articles"), f"kind={Post.Kind.ARTICLE}"),
    (_("Unanswered questions"), UNANSWERED),
)
# Empty state title per filter query string.
EMPTY_TITLES = {
    "": _("No posts yet"),
    f"kind={Post.Kind.DISCUSSION}": _("No discussions yet"),
    f"kind={Post.Kind.QUESTION}": _("No questions yet"),
    f"kind={Post.Kind.ANNOUNCEMENT}": _("No announcements yet"),
    f"kind={Post.Kind.ARTICLE}": _("No articles yet"),
    UNANSWERED: _("No unanswered questions"),
}

FIRST_POST_TEXT = _("Be the first to start a conversation in this community.")
FILTERED_EMPTY_TEXT = _("Posts matching this filter will appear here.")


def _is_htmx(request) -> bool:
    return bool(request.headers.get("HX-Request"))


def _feed_response(request, template, context, page):
    """The full page, or only the next cards and "Load more" link for an HTMX request."""
    selectors.mark_share_access(request.user, [*page.pinned, *page.items])
    context["page"] = page
    fragment = "posts/_feed_page.html" if _is_htmx(request) else template
    response = render(request, fragment, context)
    # The same URL answers a full page or a fragment: caches must keep them apart.
    patch_vary_headers(response, ["HX-Request"])
    return response


@login_required
@require_safe
def feed(request, slug):
    community, context = community_page(request, slug, "feed")
    if not context["content_visible"]:
        if community.access_mode == Community.AccessMode.OPEN or is_functional_admin(request.user):
            raise PermissionDenied
        raise Http404
    unanswered = request.GET.get("unanswered") == "1"
    kind = request.GET.get("kind") if request.GET.get("kind") in Post.Kind.values else None
    current = UNANSWERED if unanswered else (f"kind={kind}" if kind else "")
    page = selectors.community_feed(
        request.user,
        community,
        kind=kind,
        unanswered=unanswered,
        cursor=request.GET.get("cursor"),
    )
    context.update(
        {
            "filters": [(label, query, query == current) for label, query in FEED_FILTERS],
            "filter_query": current,
            "empty_title": EMPTY_TITLES[current],
            "empty_text": FIRST_POST_TEXT if not current else FILTERED_EMPTY_TEXT,
            "show_community": False,
        }
    )
    return _feed_response(request, "posts/feed.html", context, page)


@login_required
@require_safe
def home_feed(request):
    page = selectors.home_feed(request.user, cursor=request.GET.get("cursor"))
    context = {"filter_query": "", "show_community": True}
    return _feed_response(request, "posts/home_feed.html", context, page)


def _breadcrumb(post, *current):
    items = [
        (_("Communities"), reverse("communities:catalogue")),
        (post.community.name, reverse("posts:feed", args=[post.community.slug])),
    ]
    if current:
        items.append(
            (post.title, reverse("posts:detail", args=[post.community.slug, post.public_id]))
        )
        items.extend((label, "") for label in current)
    else:
        items.append((post.title, ""))
    return items


@login_required
@require_safe
def detail(request, slug, public_id):
    post = selectors.get_visible_post_or_404(request.user, slug, public_id)
    is_moderator = policies.is_content_moderator(request.user, post.community)
    comments = selectors.comment_thread(request.user, post, moderator=is_moderator)
    accepted = None
    if post.kind == Post.Kind.QUESTION and post.accepted_answer_id:
        # A hidden accepted answer stays a placeholder in the thread, never highlighted.
        accepted = next(
            (c for c in comments if c.pk == post.accepted_answer_id and c.body_visible), None
        )
    selectors.mark_share_access(request.user, [post])
    context = {
        "post": post,
        "community": post.community,
        "comments": comments,
        "accepted_answer": accepted,
        "is_moderator": is_moderator,
        "can_view_revisions": is_moderator,
        "breadcrumb": _breadcrumb(post),
    }
    return render(request, "posts/detail.html", context)


@login_required
@require_safe
def revisions(request, slug, public_id):
    post = selectors.get_visible_post_or_404(request.user, slug, public_id)
    if not policies.can_view_revisions(request.user, post):
        raise PermissionDenied
    entries = list(post.revisions.select_related("editor"))
    for revision in entries:
        revision.body_html = render_body(revision.body)
    context = {
        "post": post,
        "community": post.community,
        "revisions": entries,
        "breadcrumb": _breadcrumb(post, _("Revisions")),
    }
    return render(request, "posts/revisions.html", context)
