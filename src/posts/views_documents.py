"""Interactions on documents (L5) that live with the posts ones: bookmarks and reports.

Same conventions as ``posts.views_interactions``: POST-only actions (the report form page is
also served on GET), an HTMX request receives the partial to swap in, a plain request is
redirected (PRG, with a toast). A document the viewer may not see answers 404; a visible
document they may not report (their own one, archived) answers 403.

The document pages themselves belong to the ``documents`` app;
``views_interactions.document_url`` builds their address without failing while
``documents:detail`` is not routed.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from core.errors import DomainError
from documents import policies as document_policies
from documents.selectors import get_visible_document_or_404

from . import services_interactions as services
from .forms_interactions import ReportForm
from .models import Bookmark, BookmarkCollection
from .views_errors import domain_error_response, is_htmx
from .views_interactions import _done, _invalid, _parse_uuid, _wanted_state, document_url


def _bookmarks_url() -> str:
    return reverse("posts:bookmarks")


# Bookmarks ---------------------------------------------------------------------------------


@login_required
@require_POST
def bookmark_document(request, public_id):
    """Bookmark the document or remove the bookmark (``present``: the wanted state)."""
    document = get_visible_document_or_404(request.user, public_id)
    present = _wanted_state(request)
    if present is None:
        return HttpResponseBadRequest()
    back_url = document_url(document)
    try:
        bookmarked = services.set_document_bookmark(
            actor=request.user, document=document, present=present
        )
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=back_url)
    if is_htmx(request):
        return render(
            request,
            "posts/_document_bookmark_button.html",
            {"document": document, "bookmarked": bookmarked},
        )
    messages.success(request, _("Bookmark added.") if bookmarked else _("Bookmark removed."))
    return redirect(back_url)


@login_required
@require_POST
def bookmark_document_remove(request, public_id):
    """Remove the bookmark of a document from the bookmarks page (no read access needed)."""
    entry = get_object_or_404(
        Bookmark.objects.select_related("document"),
        user=request.user,
        document__public_id=public_id,
    )
    try:
        services.set_document_bookmark(actor=request.user, document=entry.document, present=False)
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=_bookmarks_url())
    return _done(request, _("Bookmark removed."), _bookmarks_url())


@login_required
@require_POST
def collection_move_document(request, public_id):
    """Move the bookmark of document ``public_id`` into a collection (empty value: none)."""
    entry = get_object_or_404(Bookmark, user=request.user, document__public_id=public_id)
    wanted = request.POST.get("collection", "")
    collection = (
        get_object_or_404(BookmarkCollection, user=request.user, public_id=_parse_uuid(wanted))
        if wanted
        else None
    )
    try:
        services.move_bookmark(actor=request.user, bookmark=entry, collection=collection)
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=_bookmarks_url())
    return _done(request, _("The bookmark has been moved."), _bookmarks_url())


# Reports -----------------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def report_document(request, public_id):
    """The report form of a document (GET) and its submission (POST)."""
    document = get_visible_document_or_404(request.user, public_id)
    if not document_policies.can_report_document(request.user, document):
        raise PermissionDenied
    back_url = document_url(document)
    if request.method != "POST":
        # Offer the form only when the report would be accepted (not twice).
        try:
            services.ensure_can_report_document(actor=request.user, document=document)
        except DomainError as error:
            return domain_error_response(request, error, redirect_to=back_url)
    # Bound on every POST, even an empty one (``request.POST or None`` would leave it unbound).
    form = ReportForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            services.report_document(
                actor=request.user,
                document=document,
                reason=form.cleaned_data["reason"],
                details=form.cleaned_data["details"],
            )
        except DomainError as error:
            return domain_error_response(request, error, redirect_to=back_url)
        if is_htmx(request):
            return render(request, "posts/_report_done.html", {"back_url": back_url})
        messages.success(request, _("Thank you, moderators have been informed."))
        return redirect(back_url)
    if request.method == "POST" and is_htmx(request):
        return _invalid(request, form, request.path)
    community = document.community
    context = {
        "form": form,
        "document": document,
        "community": community,
        "back_url": back_url,
        "breadcrumb": [
            (_("Communities"), reverse("communities:catalogue")),
            (community.name, reverse("communities:detail", args=[community.slug])),
            (document.title, back_url),
            (_("Report"), ""),
        ],
    }
    return render(request, "posts/document_report_form.html", context)
