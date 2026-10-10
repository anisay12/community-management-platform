"""Post editor (create, edit, preview, mention suggestions) and post actions (Task 5).

Every write goes through ``posts.services_posts``, which enforces the policies: its
``forbidden`` answers 403, ``rate_limited`` 429 with ``Retry-After``, ``edit_conflict`` 409
(the editor is shown again with the submitted text), and the other business refusals an
error toast (``views_errors.domain_error_response``) or a form error. Actions are POST-only
and follow Post/Redirect/Get with a success toast. Destructive actions (hide, archive, delete
a draft, reject a review) need ``confirmed=1``, sent by the confirmation modal; without it
(no JavaScript) the action URL answers a confirmation page instead of acting.
"""

import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from accounts.models import User
from communities.models import Community, CommunityMembership
from communities.policies import can_view_content, is_functional_admin, membership_of
from communities.selectors import visible_communities
from core.errors import DomainError
from core.templatetags.component_tags import member_name
from taxonomy.models import Tag

from . import policies, selectors, services_posts
from .forms import PostForm, ReasonForm, ReviewRejectForm, ShareForm
from .mentions import NON_ASCII, folded, handle_for, handles_of, name_key, resolve_mentions
from .models import POST_BODY_MAX_LENGTH, Comment, Post
from .rendering import render_body
from .templatetags.post_actions import creatable_kinds, publish_label, share_targets
from .views_errors import domain_error_response
from .views_moderation import REVIEW, queue_url

MENTION_SUGGESTIONS = 8
TAG_SUGGESTIONS = 200
# Service error code -> form field it is reported on.
FIELD_ERRORS = {
    "title_required": "title",
    "title_too_long": "title",
    "body_too_long": "body",
    "tag_creation_forbidden": "tags",
    "tag_unavailable": "tags",
}


def _detail_url(post) -> str:
    return reverse("posts:detail", args=[post.community.slug, post.public_id])


def _feed_url(community) -> str:
    return reverse("posts:feed", args=[community.slug])


def _readable_community(request, slug):
    """The community whose content ``request.user`` reads: 404 when the community is not
    visible, 403 when it is visible but its content is closed to the viewer (as the feed)."""
    community = get_object_or_404(visible_communities(request.user), slug=slug)
    if not can_view_content(request.user, community):
        if community.access_mode == Community.AccessMode.OPEN or is_functional_admin(request.user):
            raise PermissionDenied
        raise Http404
    return community


def _post(request, slug, public_id) -> Post:
    return selectors.get_visible_post_or_404(request.user, slug, public_id)


def _confirmed(request) -> bool:
    return request.POST.get("confirmed") == "1"


def _act(request, post, operation, success, *, redirect_to=None, error_to=None):
    """Run ``operation`` (a service call); PRG to ``redirect_to`` (default: the post), or to
    ``error_to`` (default: the post) with an error toast when the service refuses."""
    redirect_to = redirect_to or _detail_url(post)
    try:
        operation()
    except DomainError as error:
        if error.code == "forbidden":
            raise PermissionDenied from error
        return domain_error_response(request, error, redirect_to=error_to or _detail_url(post))
    messages.success(request, success)
    return redirect(redirect_to)


# --- Editor -------------------------------------------------------------------------------


def _preview(community, body: str) -> str:
    """``body`` rendered as it will be saved, mentions resolved against ``community``."""
    return render_body(body, handles_of(resolve_mentions(community, body)))


def _editor_response(request, community, form, *, post=None, preview=None, status=200, **extra):
    form.fields["body"].widget.attrs.update(
        {
            "data-mention-url": reverse("posts:mention_suggestions", args=[community.slug]),
            "aria-controls": "mention-suggestions",
        }
    )
    context = {
        "community": community,
        "post": post,
        "form": form,
        "preview": preview,
        "publish_label": publish_label(request.user, community),
        "tag_suggestions": Tag.objects.order_by("name").values_list("name", flat=True)[
            :TAG_SUGGESTIONS
        ],
        "body_max_length": POST_BODY_MAX_LENGTH,
        **extra,
    }
    return render(request, "posts/editor.html", context, status=status)


def _form_error(form, error) -> None:
    form.add_error(FIELD_ERRORS.get(error.code), error.message)


def _join_prompt(request, community):
    context = {"community": community, "join_prompt": True}
    return render(request, "posts/editor.html", context, status=403)


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def create(request, slug):
    community = _readable_community(request, slug)
    user = request.user
    kinds = creatable_kinds(user, community)
    # A functional administrator is no member but may publish announcements (framing § 4.4).
    if not kinds and membership_of(user, community) is None:
        return _join_prompt(request, community)
    if community.is_read_only:
        messages.error(request, _("This community is suspended or archived: it cannot be changed."))
        return redirect(_feed_url(community))
    can_create_tags = policies.can_create_tag(user, community)
    if request.method != "POST":
        initial_kind = request.GET.get("kind")
        if initial_kind not in kinds:
            initial_kind = Post.Kind.DISCUSSION if Post.Kind.DISCUSSION in kinds else kinds[0]
        form = PostForm(
            initial={"kind": initial_kind},
            kinds=kinds,
            can_create_tags=can_create_tags,
        )
        return _editor_response(request, community, form)
    form = PostForm(request.POST, kinds=kinds, can_create_tags=can_create_tags)
    action = request.POST.get("action")
    if not form.is_valid():
        return _editor_response(request, community, form)
    if action == "preview":
        return _editor_response(
            request, community, form, preview=_preview(community, form.cleaned_data["body"])
        )
    data = form.cleaned_data
    try:
        post = services_posts.create_post(
            actor=user,
            community=community,
            kind=data["kind"],
            title=data["title"],
            body=data["body"],
            tags=form.tag_names(),
            publish=action != "draft",
        )
    except DomainError as error:
        if error.code == "not_member":
            return _join_prompt(request, community)
        if error.code in ("forbidden", "kind_forbidden", "read_only"):
            messages.error(request, error.message)
            return _editor_response(request, community, form, status=403)
        if error.code == "rate_limited":
            return domain_error_response(request, error, redirect_to=_feed_url(community))
        _form_error(form, error)
        return _editor_response(request, community, form)
    messages.success(request, _submitted_message(post))
    return redirect(_detail_url(post))


def _submitted_message(post) -> str:
    if post.status == Post.Status.DRAFT:
        return _("Draft saved.")
    if post.status == Post.Status.PENDING_REVIEW:
        return _("Your post has been submitted for review.")
    return _("Your post has been published.")


def _tags_text(post) -> str:
    return ", ".join(tag.name for tag in post.tags.all())


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def edit(request, slug, public_id):
    post = _post(request, slug, public_id)
    community = post.community
    user = request.user
    if not policies.can_edit_post(user, post):
        raise PermissionDenied
    can_create_tags = policies.can_create_tag(user, community)
    if request.method != "POST":
        form = PostForm(
            initial={
                "title": post.title,
                "body": post.body,
                "tags": _tags_text(post),
                "version": post.version,
            },
            editing=True,
            can_create_tags=can_create_tags,
        )
        return _editor_response(request, community, form, post=post)
    form = PostForm(request.POST, editing=True, can_create_tags=can_create_tags)
    action = request.POST.get("action")
    if not form.is_valid():
        return _editor_response(request, community, form, post=post)
    data = form.cleaned_data
    if action == "preview":
        return _editor_response(
            request, community, form, post=post, preview=_preview(community, data["body"])
        )
    publish = action == "publish" and post.status == Post.Status.DRAFT
    try:
        with transaction.atomic():
            services_posts.update_post(
                actor=user,
                post=post,
                title=data["title"],
                body=data["body"],
                tags=form.tag_names(),
                version=data.get("version"),
            )
            if publish:
                services_posts.publish_draft(actor=user, post=post)
    except DomainError as error:
        if error.code == "forbidden":
            raise PermissionDenied from error
        if error.code == "edit_conflict":
            return _editor_response(
                request, community, form, post=post, status=409, conflict=error.message
            )
        if error.code in FIELD_ERRORS:
            _form_error(form, error)
            return _editor_response(request, community, form, post=post)
        return domain_error_response(request, error, redirect_to=_detail_url(post))
    messages.success(
        request, _submitted_message(post) if publish else _("Your changes have been saved.")
    )
    return redirect(_detail_url(post))


@login_required
@require_POST
def preview(request, slug):
    """The rendered Markdown of ``body`` (HTMX fragment for the editor's live region)."""
    community = _readable_community(request, slug)
    body = request.POST.get("body", "")[:POST_BODY_MAX_LENGTH]
    return render(request, "posts/_preview.html", {"preview": _preview(community, body)})


@login_required
@require_safe
def mention_suggestions(request, slug):
    """Up to ``MENTION_SUGGESTIONS`` members whose handle starts with ``q``, for members and
    for whoever may publish in the community (a functional administrator's announcement)."""
    community = _readable_community(request, slug)
    if membership_of(request.user, community) is None and not creatable_kinds(
        request.user, community
    ):
        raise PermissionDenied
    query = request.GET.get("q", "").strip().lstrip("@").lower()
    suggestions = []
    if query:
        first, dot, last = query.partition(".")
        first_key = "".join(char for char in first if char.isalnum())
        last_key = "".join(char for char in last if char.isalnum())
        members = (
            CommunityMembership.objects.filter(community=community, user__status=User.Status.ACTIVE)
            .exclude(user=request.user)
            .select_related("user")
            .alias(
                first_key=name_key(folded("user__first_name")),
                last_key=name_key(folded("user__last_name")),
                first_folded=folded("user__first_name"),
                last_folded=folded("user__last_name"),
            )
            .order_by("user__first_name", "user__last_name", "pk")
        )
        # SQL pre-filter on the accent-free names; the exact handle is checked below.
        members = members.filter(
            Q(first_key__startswith=first_key) | Q(first_folded__regex=NON_ASCII)
        )
        if dot:
            members = members.filter(
                Q(last_key__startswith=last_key) | Q(last_folded__regex=NON_ASCII)
            )
        for membership in members.iterator(chunk_size=200):
            handle = handle_for(membership.user)
            if handle.startswith(query):
                suggestions.append((handle, member_name(membership.user)))
                if len(suggestions) == MENTION_SUGGESTIONS:
                    break
    return render(request, "posts/_mention_suggestions.html", {"suggestions": suggestions})


# --- Actions ------------------------------------------------------------------------------


@login_required
@require_POST
def publish(request, slug, public_id):
    post = _post(request, slug, public_id)
    try:
        services_posts.publish_draft(actor=request.user, post=post)
    except DomainError as error:
        if error.code == "forbidden":
            raise PermissionDenied from error
        return domain_error_response(request, error, redirect_to=_detail_url(post))
    messages.success(request, _submitted_message(post))
    return redirect(_detail_url(post))


def _confirmation(request, post, *, title, text, action_url, confirm_label, form=None):
    """The no-JavaScript confirmation page of a destructive action."""
    context = {
        "post": post,
        "community": post.community,
        "title": title,
        "text": text,
        "action_url": action_url,
        "confirm_label": confirm_label,
        "form": form,
    }
    return render(request, "posts/hide_confirm.html", context)


@login_required
@require_POST
def delete_draft(request, slug, public_id):
    post = _post(request, slug, public_id)
    if not policies.can_delete_draft(request.user, post):
        raise PermissionDenied
    if not _confirmed(request):
        return _confirmation(
            request,
            post,
            title=_("Delete this draft?"),
            text=_("The draft will be permanently deleted."),
            action_url=reverse("posts:delete_draft", args=[slug, public_id]),
            confirm_label=_("Delete"),
        )
    community = post.community
    return _act(
        request,
        post,
        lambda: services_posts.delete_draft(actor=request.user, post=post),
        _("The draft has been deleted."),
        redirect_to=_feed_url(community),
    )


@login_required
@require_POST
def pin(request, slug, public_id):
    post = _post(request, slug, public_id)
    return _act(
        request,
        post,
        lambda: services_posts.pin_post(actor=request.user, post=post),
        _("The post has been pinned."),
    )


@login_required
@require_POST
def unpin(request, slug, public_id):
    post = _post(request, slug, public_id)
    return _act(
        request,
        post,
        lambda: services_posts.unpin_post(actor=request.user, post=post),
        _("The post has been unpinned."),
    )


@login_required
@require_POST
def hide(request, slug, public_id):
    post = _post(request, slug, public_id)
    if not policies.can_moderate(request.user, post.community):
        raise PermissionDenied
    form = ReasonForm(request.POST if _confirmed(request) else None)
    if not form.is_valid():
        return _confirmation(
            request,
            post,
            title=_("Hide this post?"),
            text=_("Readers will no longer see it. Its author keeps reading it, with the reason."),
            action_url=reverse("posts:hide", args=[slug, public_id]),
            confirm_label=_("Hide"),
            form=form,
        )
    return _act(
        request,
        post,
        lambda: services_posts.hide_post(
            actor=request.user, post=post, reason=form.cleaned_data["reason"]
        ),
        _("The post has been hidden."),
    )


@login_required
@require_POST
def unhide(request, slug, public_id):
    post = _post(request, slug, public_id)
    return _act(
        request,
        post,
        lambda: services_posts.unhide_post(actor=request.user, post=post),
        _("The post is visible again."),
    )


@login_required
@require_POST
def archive(request, slug, public_id):
    post = _post(request, slug, public_id)
    if not policies.can_moderate(request.user, post.community):
        raise PermissionDenied
    if not _confirmed(request):
        return _confirmation(
            request,
            post,
            title=_("Archive this post?"),
            text=_("It stays readable by link but leaves the feeds and can no longer change."),
            action_url=reverse("posts:archive", args=[slug, public_id]),
            confirm_label=_("Archive"),
        )
    return _act(
        request,
        post,
        lambda: services_posts.archive_post(actor=request.user, post=post),
        _("The post has been archived."),
    )


@login_required
@require_POST
def accept_answer(request, slug, public_id):
    """Accept the comment ``comment`` (public id) as the answer, or clear it (``clear=1``).
    Rights come first: a reader who may not decide learns nothing about the comments."""
    post = _post(request, slug, public_id)
    if not policies.may_decide_answers(request.user, post):
        raise PermissionDenied
    if request.POST.get("clear") == "1":
        return _act(
            request,
            post,
            lambda: services_posts.clear_accepted_answer(actor=request.user, post=post),
            _("The accepted answer has been cleared."),
        )
    comment = Comment.objects.filter(
        post=post, public_id=_uuid_or_404(request.POST.get("comment"))
    ).first()
    if comment is None:
        raise Http404
    return _act(
        request,
        post,
        lambda: services_posts.accept_answer(actor=request.user, post=post, comment=comment),
        _("The answer has been accepted."),
    )


def _uuid_or_404(value):
    try:
        return uuid.UUID(str(value))
    except ValueError as error:
        raise Http404 from error


@login_required
@require_POST
def review_decide(request, slug, public_id):
    """Approve or reject a pending post, from the post page or the moderation queue
    (``from=queue``: the decision returns to the queue's review tab).

    Rejecting needs a note: without one the note form is shown (again), keeping what was
    typed. A post that is no longer pending (decided elsewhere) answers an error toast.
    """
    post = _post(request, slug, public_id)
    decision = request.POST.get("decision")
    if decision not in ("approve", "reject"):
        return HttpResponseBadRequest()
    if not policies.can_review(request.user, post.community):
        raise PermissionDenied
    from_queue = request.POST.get("from") == "queue"
    back_url = queue_url(post.community, REVIEW) if from_queue else _detail_url(post)
    if decision == "approve":
        return _act(
            request,
            post,
            lambda: services_posts.approve_review(actor=request.user, post=post),
            _("The post has been approved and published."),
            redirect_to=back_url,
            error_to=back_url,
        )
    note = ""
    if post.status == Post.Status.PENDING_REVIEW:
        form = ReviewRejectForm(request.POST if _confirmed(request) else None)
        if not form.is_valid():
            context = {
                "post": post,
                "community": post.community,
                "form": form,
                "from_queue": from_queue,
                "back_url": back_url,
            }
            return render(request, "posts/review_reject.html", context)
        note = form.cleaned_data["note"]
    # Otherwise the service refuses (``invalid_state``), whatever the note.
    return _act(
        request,
        post,
        lambda: services_posts.reject_review(actor=request.user, post=post, note=note),
        _("The post has been sent back to its author."),
        redirect_to=back_url,
        error_to=back_url,
    )


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def share(request, slug, public_id):
    post = _post(request, slug, public_id)
    targets = share_targets(request.user, post)
    if request.method != "POST":
        form = ShareForm(initial={"title": post.title}, targets=targets)
    else:
        form = ShareForm(request.POST, targets=targets)
        if form.is_valid():
            target = next(c for c in targets if c.slug == form.cleaned_data["target"])
            try:
                shared = services_posts.share_post(
                    actor=request.user,
                    post=post,
                    target_community=target,
                    title=form.cleaned_data["title"],
                    comment=form.cleaned_data["comment"],
                )
            except DomainError as error:
                if error.code == "forbidden":
                    raise PermissionDenied from error
                if error.code == "rate_limited":
                    return domain_error_response(request, error, redirect_to=_detail_url(post))
                _form_error(form, error)
            else:
                messages.success(request, _submitted_message(shared))
                return redirect(_detail_url(shared))
    context = {"post": post, "community": post.community, "form": form, "targets": targets}
    return render(request, "posts/share.html", context)
