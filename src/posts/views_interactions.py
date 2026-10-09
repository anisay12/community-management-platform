"""Interaction pages: comments, reactions, bookmarks and collections, content reports.

Actions are POST-only. A plain request redirects back to the post (PRG, with a toast); an HTMX
request receives the partial to swap in (the comment, the reaction bar, the bookmark button).
A refused business rule goes through ``posts.views_errors.domain_error_response`` (error toast,
429 for the rate limit), never a server error. A post or comment the viewer may not see answers
404, whatever the action.
"""

import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.db.models.functions import Lower
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from core.errors import DomainError

from . import policies, selectors
from . import services_interactions as services
from .forms_interactions import CollectionForm, CommentForm, HideForm, ReportForm
from .models import Bookmark, BookmarkCollection, Comment, Post, Reaction
from .views_errors import domain_error_response

BOOKMARKS_PAGE_SIZE = 20
NO_COLLECTION = "none"


def _is_htmx(request) -> bool:
    return bool(request.headers.get("HX-Request"))


def _detail_url(post) -> str:
    return reverse("posts:detail", args=[post.community.slug, post.public_id])


def _comment_url(comment) -> str:
    return f"{_detail_url(comment.post)}#comment-{comment.public_id}"


def _target_url(target) -> str:
    return _comment_url(target) if isinstance(target, Comment) else _detail_url(target)


def _parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise Http404 from None


def _visible_post(user, public_id) -> Post:
    return get_object_or_404(
        Post.objects.visible_to(user).select_related("community", "author"), public_id=public_id
    )


def _visible_comment(user, public_id) -> Comment:
    comment = get_object_or_404(
        Comment.objects.select_related("post__community", "post__author", "parent"),
        public_id=public_id,
    )
    if not policies.can_view_comment(user, comment):
        raise Http404
    return comment


def _target(user, target_type, public_id):
    if target_type == "post":
        return _visible_post(user, public_id)
    if target_type == "comment":
        return _visible_comment(user, public_id)
    raise Http404


def _done(request, message, redirect_to):
    """Success of an action whose HTMX answer is a reload: toast, then redirect."""
    messages.success(request, message)
    if _is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = redirect_to
        return response
    return redirect(redirect_to)


def _invalid(request, form, redirect_to):
    """A form the browser should have refused (length attributes bypassed): error toast."""
    first = next(iter(form.errors.values()))[0]
    return domain_error_response(request, DomainError("invalid", first), redirect_to=redirect_to)


# Comments ----------------------------------------------------------------------------------


def _render_thread_item(request, comment):
    """The top-level comment of ``comment`` with its replies, as on the post page."""
    top = comment.parent if comment.parent_id else comment
    post = top.post = comment.post
    user = request.user
    moderator = policies.is_content_moderator(user, post.community)
    replies = list(Comment.objects.filter(parent=top).order_by("created_at", "pk"))
    for reply in replies:
        reply.parent = top
    top.prefetched_replies = replies
    for item in (top, *replies):
        item.body_visible = (
            item.status == Comment.Status.VISIBLE or moderator or item.author_id == user.pk
        )
    context = {"comment": top, "post": post, "reply": False, "is_moderator": moderator}
    return render(request, "posts/_comment.html", context)


@login_required
@require_POST
def comment_create(request, slug, public_id):
    post = selectors.get_visible_post_or_404(request.user, slug, public_id)
    form = CommentForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest()
    parent = None
    if form.cleaned_data["parent"]:
        parent = get_object_or_404(
            Comment.objects.select_related("parent"),
            post=post,
            public_id=form.cleaned_data["parent"],
        )
    try:
        comment = services.add_comment(
            actor=request.user, post=post, body=form.cleaned_data["body"], parent=parent
        )
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=_detail_url(post))
    if not _is_htmx(request):
        messages.success(request, _("Your comment has been published."))
        return redirect(_comment_url(comment))
    if comment.parent_id:
        return _render_thread_item(request, comment)
    comment.prefetched_replies = []
    comment.body_visible = True
    return render(request, "posts/_comment_created.html", {"comment": comment, "post": post})


@login_required
@require_POST
def comment_edit(request, public_id):
    comment = _visible_comment(request.user, public_id)
    form = CommentForm(request.POST)
    if not form.is_valid():
        return _invalid(request, form, _comment_url(comment))
    try:
        services.update_comment(actor=request.user, comment=comment, body=form.cleaned_data["body"])
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=_comment_url(comment))
    if _is_htmx(request):
        return _render_thread_item(request, comment)
    messages.success(request, _("Your comment has been updated."))
    return redirect(_comment_url(comment))


@login_required
@require_POST
def comment_hide(request, public_id):
    comment = _visible_comment(request.user, public_id)
    form = HideForm(request.POST)
    if not form.is_valid():
        return _invalid(request, form, _comment_url(comment))
    try:
        services.hide_comment(
            actor=request.user, comment=comment, reason=form.cleaned_data["reason"].strip()
        )
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=_comment_url(comment))
    if _is_htmx(request):
        comment.refresh_from_db()
        return _render_thread_item(request, comment)
    messages.success(request, _("The comment is hidden."))
    return redirect(_comment_url(comment))


@login_required
@require_POST
def comment_unhide(request, public_id):
    comment = _visible_comment(request.user, public_id)
    try:
        services.unhide_comment(actor=request.user, comment=comment)
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=_comment_url(comment))
    if _is_htmx(request):
        comment.refresh_from_db()
        return _render_thread_item(request, comment)
    messages.success(request, _("The comment is visible again."))
    return redirect(_comment_url(comment))


# Reactions ---------------------------------------------------------------------------------


@login_required
@require_POST
def react(request, target, public_id):
    target_type = target
    target = _target(request.user, target_type, public_id)
    kind = request.POST.get("kind", "")
    # Counters are deferred: the bar shows the stored counts adjusted by this toggle.
    counts = dict(target.reaction_counts or {})
    try:
        present = services.toggle_reaction(actor=request.user, target=target, kind=kind)
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=_target_url(target))
    if not _is_htmx(request):
        return redirect(_target_url(target))
    current = counts.get(kind) if isinstance(counts.get(kind), int) else 0
    counts[kind] = current + 1 if present else max(current - 1, 0)
    mine = set(
        Reaction.objects.filter(user=request.user, **{target_type: target}).values_list(
            "kind", flat=True
        )
    )
    context = {
        "target": target,
        "target_type": target_type,
        "mine": mine,
        "counts": counts,
        "interactive": True,
    }
    return render(request, "posts/_reaction_bar_inner.html", context)


# Bookmarks ---------------------------------------------------------------------------------


@login_required
@require_POST
def bookmark_toggle(request, slug, public_id):
    post = selectors.get_visible_post_or_404(request.user, slug, public_id)
    try:
        bookmarked = services.toggle_bookmark(actor=request.user, post=post)
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=_detail_url(post))
    if _is_htmx(request):
        return render(
            request, "posts/_bookmark_button.html", {"post": post, "bookmarked": bookmarked}
        )
    messages.success(request, _("Bookmark added.") if bookmarked else _("Bookmark removed."))
    return redirect(_detail_url(post))


@login_required
@require_safe
def bookmarks(request):
    user = request.user
    collections = list(
        BookmarkCollection.objects.filter(user=user)
        .annotate(entry_count=Count("bookmarks"))
        .order_by(Lower("name"), "pk")
    )
    entries = (
        Bookmark.objects.filter(user=user)
        .select_related("post__community", "collection")
        .order_by("-created_at", "-pk")
    )
    selected = request.GET.get("collection", "")
    current = None
    if selected == NO_COLLECTION:
        entries = entries.filter(collection__isnull=True)
    elif selected:
        wanted = _parse_uuid(selected)
        current = next((c for c in collections if c.public_id == wanted), None)
        if current is None:
            raise Http404
        entries = entries.filter(collection=current)
    page_obj = Paginator(entries, BOOKMARKS_PAGE_SIZE).get_page(request.GET.get("page"))
    post_ids = [entry.post_id for entry in page_obj]
    visible = set(
        Post.objects.visible_to(user).filter(pk__in=post_ids).values_list("pk", flat=True)
    )
    for entry in page_obj:
        entry.accessible = entry.post_id in visible
    context = {
        "collections": collections,
        "current": current,
        "selected": selected,
        "page_obj": page_obj,
        "querystring": f"collection={selected}" if selected else "",
        "max_name_length": services.COLLECTION_NAME_MAX_LENGTH,
    }
    return render(request, "posts/bookmarks.html", context)


def _bookmarks_url():
    return reverse("posts:bookmarks")


def _own_collection(user, public_id) -> BookmarkCollection:
    return get_object_or_404(BookmarkCollection, user=user, public_id=public_id)


@login_required
@require_POST
def bookmark_remove(request, public_id):
    entry = get_object_or_404(
        Bookmark.objects.select_related("post"), user=request.user, post__public_id=public_id
    )
    services.toggle_bookmark(actor=request.user, post=entry.post)  # removing needs no access
    return _done(request, _("Bookmark removed."), _bookmarks_url())


@login_required
@require_POST
def collection_create(request):
    form = CollectionForm(request.POST)
    if not form.is_valid():
        return _invalid(request, form, _bookmarks_url())
    try:
        services.create_collection(actor=request.user, name=form.cleaned_data["name"])
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=_bookmarks_url())
    return _done(request, _("The collection has been created."), _bookmarks_url())


@login_required
@require_POST
def collection_rename(request, public_id):
    collection = _own_collection(request.user, public_id)
    form = CollectionForm(request.POST)
    if not form.is_valid():
        return _invalid(request, form, _bookmarks_url())
    try:
        services.rename_collection(
            actor=request.user, collection=collection, name=form.cleaned_data["name"]
        )
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=_bookmarks_url())
    return _done(request, _("The collection has been renamed."), _bookmarks_url())


@login_required
@require_POST
def collection_delete(request, public_id):
    collection = _own_collection(request.user, public_id)
    services.delete_collection(actor=request.user, collection=collection)
    return _done(request, _("The collection has been deleted."), _bookmarks_url())


@login_required
@require_POST
def collection_move(request, public_id):
    """Move the bookmark of post ``public_id`` into a collection (empty value: none)."""
    entry = get_object_or_404(Bookmark, user=request.user, post__public_id=public_id)
    wanted = request.POST.get("collection", "")
    collection = _own_collection(request.user, _parse_uuid(wanted)) if wanted else None
    services.move_bookmark(actor=request.user, bookmark=entry, collection=collection)
    return _done(request, _("The bookmark has been moved."), _bookmarks_url())


# Reports -----------------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def report(request, target, public_id):
    target_type = target
    target = _target(request.user, target_type, public_id)
    post = target.post if isinstance(target, Comment) else target
    back_url = _target_url(target)
    form = ReportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.report(
                actor=request.user,
                target=target,
                reason=form.cleaned_data["reason"],
                details=form.cleaned_data["details"],
            )
        except DomainError as error:
            return domain_error_response(request, error, redirect_to=back_url)
        if _is_htmx(request):
            return render(request, "posts/_report_done.html", {"back_url": back_url})
        messages.success(request, _("Thank you, moderators have been informed."))
        return redirect(back_url)
    if request.method == "POST" and _is_htmx(request):
        return _invalid(request, form, request.path)
    context = {
        "form": form,
        "target": target,
        "target_type": target_type,
        "post": post,
        "community": post.community,
        "back_url": back_url,
        "breadcrumb": [
            (_("Communities"), reverse("communities:catalogue")),
            (post.community.name, reverse("posts:feed", args=[post.community.slug])),
            (post.title, _detail_url(post)),
            (_("Report"), ""),
        ],
    }
    return render(request, "posts/report_form.html", context)
