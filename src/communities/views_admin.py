"""Functional administration of communities: categories and creation requests.

Reserved to functional administrators and superusers (404 for anyone else); MFA is enforced
by the middleware on ``/manage/``.
"""

from functools import wraps

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Value, When
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.errors import DomainError

from . import services
from .forms_admin import CategoryForm, CreationDecisionForm
from .models import CommunityCategory, CommunityCreationRequest
from .policies import can_manage_categories, is_functional_admin

PAGE_SIZE = 20


def _admin_only(policy):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not policy(request.user):
                raise Http404
            return view(request, *args, **kwargs)

        return never_cache(wrapped)

    return decorator


categories_required = _admin_only(can_manage_categories)
functional_admin_required = _admin_only(is_functional_admin)


@categories_required
def category_list(request):
    categories = CommunityCategory.objects.annotate(community_count=Count("communities"))
    return render(request, "manage/category_list.html", {"categories": categories})


@transaction.atomic
def _save_category(actor, category, data) -> CommunityCategory:
    """Create or update ``category`` from the form data through the services."""
    if category is None:
        return services.create_category(actor=actor, **data)
    fields = {key: data[key] for key in ("name", "description", "icon", "order")}
    if data["slug"]:
        fields["slug"] = data["slug"]
    if data["is_active"]:
        fields["is_active"] = True
    category = services.update_category(actor=actor, category=category, **fields)
    if not data["is_active"] and category.is_active:
        category = services.deactivate_category(actor=actor, category=category)
    return category


def _category_form(request, category=None):
    form = CategoryForm(request.POST if request.method == "POST" else None, instance=category)
    if request.method == "POST" and form.is_valid():
        try:
            _save_category(request.user, category, form.cleaned_data)
        except DomainError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, _("The category has been saved."))
            return redirect("manage:category_list")
    return render(request, "manage/category_form.html", {"form": form, "category": category})


@categories_required
def category_create(request):
    return _category_form(request)


@categories_required
def category_edit(request, slug):
    return _category_form(request, get_object_or_404(CommunityCategory, slug=slug))


@functional_admin_required
def creation_request_list(request):
    pending = CommunityCreationRequest.Status.PENDING
    requests = (
        CommunityCreationRequest.objects.select_related("requester", "category", "community")
        .annotate(
            pending_first=Case(
                When(status=pending, then=Value(0)), default=Value(1), output_field=IntegerField()
            )
        )
        .order_by("pending_first", "-created_at")
    )
    paginator = Paginator(requests, PAGE_SIZE)
    return render(
        request,
        "manage/creation_request_list.html",
        {"page_obj": paginator.get_page(request.GET.get("page"))},
    )


@functional_admin_required
@require_POST
def creation_request_decide(request, public_id):
    creation_request = get_object_or_404(CommunityCreationRequest, public_id=public_id)
    form = CreationDecisionForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest()
    note = form.cleaned_data["note"]
    try:
        if form.cleaned_data["decision"] == "approve":
            services.approve_creation_request(
                actor=request.user, request=creation_request, note=note
            )
            messages.success(request, _("The community has been created."))
        else:
            services.reject_creation_request(
                actor=request.user, request=creation_request, note=note
            )
            messages.success(request, _("The request has been rejected."))
    except DomainError as error:
        messages.error(request, error.message)
    return redirect("manage:creation_request_list")
