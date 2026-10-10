"""Document pages (L5): the community Resources tab, upload, document page, metadata edition,
new versions, reference version, archiving and links to posts.

Conventions (as the posts views): a community or document the viewer may not see answers 404,
a refused action on a visible one answers 403; writes are POST only and call one service; a
``DomainError`` becomes a form error when a form is redisplayed, otherwise a toast and a
redirect (``posts.views_errors.domain_error_response``, which also answers 429 to
``rate_limited``). Uploads are idempotent (``core.idempotency``): a second submission of the
same form redirects to the result of the first one.
"""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from communities.models import Community
from communities.policies import is_functional_admin
from communities.views import community_page
from core import idempotency
from core.errors import DomainError
from posts.models import Bookmark, Post
from posts.policies import can_create_tag
from posts.views_errors import domain_error_response
from taxonomy.models import Tag

from . import policies, selectors_pages, services
from .forms import (
    DocumentMetadataForm,
    DocumentUploadForm,
    LinkPostForm,
    ResourcesFilterForm,
    VersionUploadForm,
)
from .models import DocumentLink, DocumentVersion
from .selectors import get_visible_document_or_404

PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
TAG_SUGGESTIONS = 200
PREVIEWABLE_TYPES = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}
)
# Service error code -> form field it is reported on (``None``: the whole form).
FIELD_ERRORS = {
    "title_required": "title",
    "title_too_long": "title",
    "description_too_long": "description",
    "invalid_date": "expires_at",
    "invalid_choice": None,
    "tag_creation_forbidden": "tags",
    "tag_unavailable": "tags",
    "change_note_too_long": "change_note",
    "version_label_too_long": "version_label",
    "version_label_taken": "version_label",
    "file_empty": "file",
    "file_too_large": "file",
    "extension_refused": "file",
    "type_mismatch": "file",
    "storage_unavailable": None,
}


def detail_url(document) -> str:
    return reverse("documents:detail", args=[document.public_id])


def resources_url(community) -> str:
    return reverse("documents:community_documents", args=[community.slug])


def _page_size(request) -> int:
    try:
        size = int(request.GET.get("per_page", PAGE_SIZE))
    except ValueError:
        return PAGE_SIZE
    return max(1, min(size, MAX_PAGE_SIZE))


def _visible_community_with_content(request, slug, tab="resources"):
    """The community page context; 404 when invisible, 403 when its content is closed (as the
    Feed tab)."""
    community, context = community_page(request, slug, tab)
    if not context["content_visible"]:
        if community.access_mode == Community.AccessMode.OPEN or is_functional_admin(request.user):
            raise PermissionDenied
        raise Http404
    return community, context


def _form_error(form, error) -> None:
    field = FIELD_ERRORS.get(error.code)
    if field not in form.fields:
        field = None
    form.add_error(field, error.message)


def _tag_suggestions():
    return Tag.objects.order_by("name").values_list("name", flat=True)[:TAG_SUGGESTIONS]


# Resources tab ---------------------------------------------------------------------------


@login_required
@require_safe
def community_documents(request, slug):
    community, context = _visible_community_with_content(request, slug)
    user = request.user
    show_status = policies.is_document_moderator(
        user, community
    ) or selectors_pages.owns_documents_in(user, community)
    tags = list(selectors_pages.community_document_tags(user, community))
    form = ResourcesFilterForm(request.GET or None, tags=tags, show_status=show_status)
    filters = form.filters() if form.is_bound else ResourcesFilterForm().filters()
    if not show_status:
        filters["status"] = ""
    documents = selectors_pages.community_documents(user, community, **filters)
    page_obj = Paginator(documents, _page_size(request)).get_page(request.GET.get("page"))
    querystring = urlencode(
        [(key, value) for key, value in request.GET.items() if key != "page" and value]
    )
    filtered = any(filters[key] for key in ("query", "doc_type", "tag", "status"))
    context.update(
        {
            "form": form,
            "page_obj": page_obj,
            "querystring": querystring,
            "filtered": filtered,
            "can_upload": policies.can_upload(user, community),
        }
    )
    return render(request, "documents/community_documents.html", context)


# Upload ----------------------------------------------------------------------------------


def _breadcrumb(community, *items):
    return [
        (_("Communities"), reverse("communities:catalogue")),
        (community.name, resources_url(community)),
        *items,
    ]


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def create(request, slug):
    community, _context = community_page(request, slug, "resources")
    user = request.user
    if not policies.can_upload(user, community):
        raise PermissionDenied
    can_create_tags = can_create_tag(user, community)
    form = DocumentUploadForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        can_create_tags=can_create_tags,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data

        def operation():
            document = services.create_document(
                actor=user,
                community=community,
                upload=data["file"],
                change_note=data["change_note"],
                version_label=data["version_label"],
                **form.metadata(),
            )
            return str(document.public_id)

        try:
            public_id, replayed = idempotency.run_once(
                user, f"documents:create:{community.pk}", data["idempotency_key"], operation
            )
        except DomainError as error:
            if error.code in ("rate_limited", "submission_in_progress"):
                return domain_error_response(request, error, redirect_to=resources_url(community))
            _form_error(form, error)
        else:
            if not replayed:
                messages.success(
                    request,
                    _(
                        "The document has been uploaded. The file is being scanned for viruses: "
                        "it becomes available to readers once the scan is clean."
                    ),
                )
            return redirect("documents:detail", public_id=public_id)
    context = {
        "community": community,
        "form": form,
        "tag_suggestions": _tag_suggestions(),
        "breadcrumb": _breadcrumb(community, (_("Upload a document"), "")),
    }
    return render(request, "documents/document_form.html", context)


# Document page ---------------------------------------------------------------------------


@login_required
@require_safe
def detail(request, public_id):
    user = request.user
    document = get_visible_document_or_404(user, public_id)
    community = document.community
    reference = document.current_version
    versions = []
    for version in selectors_pages.document_versions(document):
        version.document = document
        if not policies.can_view_version(user, version):
            continue
        version.can_download = policies.can_download(user, document, version)
        version.can_preview = version.can_download and _previewable(version)
        version.can_set_reference = not version.is_reference and policies.can_set_reference(
            user, version
        )
        versions.append(version)
    can_edit = policies.can_edit_document(user, document)
    can_download = policies.can_download(user, document)
    context = {
        "document": document,
        "community": community,
        "reference": reference,
        "versions": versions,
        "links": selectors_pages.document_links(user, document),
        "can_edit": can_edit,
        "can_download": can_download,
        "can_preview": can_download
        and reference is not None
        and reference.mime_type in PREVIEWABLE_TYPES,
        "can_archive": policies.can_archive_document(user, document),
        "is_manager": policies.is_document_manager(user, document),
        "can_bookmark": policies.can_bookmark_document(user, document),
        "can_report": policies.can_report_document(user, document),
        "bookmarked": Bookmark.objects.filter(user=user, document=document).exists(),
        "link_form": (
            LinkPostForm(posts=selectors_pages.linkable_posts(user, document)) if can_edit else None
        ),
        "breadcrumb": _breadcrumb(community, (document.title, "")),
        "resources_url": resources_url(community),
    }
    return render(request, "documents/detail.html", context)


def _previewable(version) -> bool:
    return version is not None and version.mime_type in PREVIEWABLE_TYPES


def _editable_document(request, public_id):
    document = get_visible_document_or_404(request.user, public_id)
    if not policies.can_edit_document(request.user, document):
        raise PermissionDenied
    return document


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def edit(request, public_id):
    user = request.user
    document = _editable_document(request, public_id)
    community = document.community
    can_create_tags = can_create_tag(user, community)
    if request.method == "POST":
        form = DocumentMetadataForm(request.POST, can_create_tags=can_create_tags)
        if form.is_valid():
            try:
                services.update_document(actor=user, document=document, **form.metadata())
            except DomainError as error:
                if error.code == "rate_limited":
                    return domain_error_response(request, error, redirect_to=detail_url(document))
                _form_error(form, error)
            else:
                messages.success(request, _("The document has been updated."))
                return redirect(detail_url(document))
    else:
        form = DocumentMetadataForm(
            initial=DocumentMetadataForm.initial_for(document), can_create_tags=can_create_tags
        )
    context = {
        "community": community,
        "document": document,
        "form": form,
        "tag_suggestions": _tag_suggestions(),
        "breadcrumb": _breadcrumb(
            community, (document.title, detail_url(document)), (_("Edit"), "")
        ),
    }
    return render(request, "documents/document_form.html", context)


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def add_version(request, public_id):
    user = request.user
    document = _editable_document(request, public_id)
    community = document.community
    form = VersionUploadForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data

        def operation():
            version = services.add_version(
                actor=user,
                document=document,
                upload=data["file"],
                change_note=data["change_note"],
                version_label=data["version_label"],
                make_reference=data["make_reference"],
            )
            return version.number

        try:
            _number, replayed = idempotency.run_once(
                user, f"documents:version:{document.pk}", data["idempotency_key"], operation
            )
        except DomainError as error:
            if error.code in ("rate_limited", "submission_in_progress"):
                return domain_error_response(request, error, redirect_to=detail_url(document))
            _form_error(form, error)
        else:
            if not replayed:
                messages.success(
                    request,
                    _(
                        "The new version has been uploaded. The file is being scanned for "
                        "viruses before it can be downloaded."
                    ),
                )
            return redirect(detail_url(document))
    context = {
        "community": community,
        "document": document,
        "form": form,
        "breadcrumb": _breadcrumb(
            community, (document.title, detail_url(document)), (_("New version"), "")
        ),
    }
    return render(request, "documents/version_form.html", context)


# Actions (POST only) ---------------------------------------------------------------------


def _done(request, message, url):
    messages.success(request, message)
    return redirect(url)


@login_required
@require_POST
def set_reference(request, public_id, number):
    user = request.user
    document = get_visible_document_or_404(user, public_id)
    version = get_object_or_404(DocumentVersion, document=document, number=number)
    version.document = document
    if not policies.can_view_version(user, version):
        raise Http404
    if not policies.can_edit_document(user, document):
        raise PermissionDenied
    try:
        services.set_reference_version(actor=user, version=version)
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=detail_url(document))
    return _done(
        request,
        _("Version %(label)s is now the reference version.") % {"label": version.version_label},
        detail_url(document),
    )


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def archive(request, public_id):
    """Confirmation page (GET, the no-JavaScript path of the modal) and archiving (POST)."""
    user = request.user
    document = get_visible_document_or_404(user, public_id)
    if not policies.can_archive_document(user, document):
        raise PermissionDenied
    if request.method == "POST":
        try:
            services.archive_document(actor=user, document=document)
        except DomainError as error:
            return domain_error_response(request, error, redirect_to=detail_url(document))
        return _done(request, _("The document has been archived."), detail_url(document))
    context = {
        "community": document.community,
        "document": document,
        "breadcrumb": _breadcrumb(
            document.community, (document.title, detail_url(document)), (_("Archive"), "")
        ),
    }
    return render(request, "documents/archive_confirm.html", context)


@login_required
@require_POST
def restore(request, public_id):
    user = request.user
    document = get_visible_document_or_404(user, public_id)
    if not policies.can_archive_document(user, document):
        raise PermissionDenied
    try:
        services.restore_document(actor=user, document=document)
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=detail_url(document))
    return _done(request, _("The document has been restored."), detail_url(document))


@login_required
@require_POST
def link(request, public_id):
    user = request.user
    document = _editable_document(request, public_id)
    posts = selectors_pages.linkable_posts(user, document)
    form = LinkPostForm(request.POST, posts=posts)
    if not form.is_valid():
        messages.error(request, _("Choose a post of this community."))
        return redirect(detail_url(document))
    post = get_object_or_404(
        Post.objects.visible_to(user).select_related("community"),
        public_id=form.cleaned_data["post"],
    )
    try:
        services.link_document(actor=user, document=document, post=post)
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=detail_url(document))
    return _done(request, _("The document has been linked to the post."), detail_url(document))


@login_required
@require_POST
def unlink(request, public_id, link_id):
    user = request.user
    document = _editable_document(request, public_id)
    entry = get_object_or_404(
        DocumentLink.objects.select_related("post"), document=document, pk=link_id
    )
    try:
        services.unlink_document(actor=user, link=entry)
    except DomainError as error:
        return domain_error_response(request, error, redirect_to=detail_url(document))
    return _done(request, _("The link has been removed."), detail_url(document))
