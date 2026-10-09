"""Account administration pages, reserved to functional administrators and superusers."""

from functools import wraps

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.errors import DomainError

from . import services
from .forms import UserCreateForm, UserImportForm, UserRolesForm, UserStatusForm
from .models import User
from .policies import can_manage_users
from .roles import user_roles
from .selectors import users_for_admin

PAGE_SIZE = 20

STATUS_ACTIONS = {
    "suspend": (services.suspend_user, gettext_lazy("The account has been suspended.")),
    "reactivate": (services.reactivate_user, gettext_lazy("The account has been reactivated.")),
    "deactivate": (services.deactivate_user, gettext_lazy("The account has been deactivated.")),
    "resend_activation": (
        services.resend_activation,
        gettext_lazy("A new activation email has been sent."),
    ),
}


def manage_required(view):
    """Answer 404 to anyone who may not manage users: the pages are not revealed."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_manage_users(request.user):
            raise Http404
        return view(request, *args, **kwargs)

    return never_cache(wrapped)


def _account(public_id) -> User:
    return get_object_or_404(User, public_id=public_id)


@manage_required
def user_list(request):
    query = request.GET.get("q", "")
    status = request.GET.get("status", "")
    paginator = Paginator(users_for_admin(query=query, status=status), PAGE_SIZE)
    context = {
        "page_obj": paginator.get_page(request.GET.get("page")),
        "query": query,
        "status": status,
        "statuses": User.Status.choices,
    }
    return render(request, "manage/user_list.html", context)


@manage_required
def user_create(request):
    form = UserCreateForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            account = services.create_user(
                actor=request.user,
                email=data["email"],
                first_name=data["first_name"],
                last_name=data["last_name"],
                unit=data["unit"],
                manager=data["manager_email"],
                roles=data["roles"],
            )
        except DomainError as error:
            form.add_error("email" if error.code == "email_taken" else None, error.message)
        else:
            messages.success(
                request, _("The account has been created and an activation email sent.")
            )
            return redirect("manage:user_detail", public_id=account.public_id)
    return render(request, "manage/user_form.html", {"form": form})


@manage_required
def user_detail(request, public_id):
    account = get_object_or_404(
        User.objects.select_related("employment__unit", "employment__manager"),
        public_id=public_id,
    )
    context = {
        "account": account,
        "roles_form": UserRolesForm(initial={"roles": sorted(user_roles(account))}),
    }
    return render(request, "manage/user_detail.html", context)


@manage_required
@require_POST
def user_status(request, public_id):
    account = _account(public_id)
    form = UserStatusForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest()
    service, success = STATUS_ACTIONS[form.cleaned_data["action"]]
    try:
        service(actor=request.user, user=account)
    except DomainError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, success)
    return redirect("manage:user_detail", public_id=account.public_id)


@manage_required
@require_POST
def user_roles_update(request, public_id):
    account = _account(public_id)
    form = UserRolesForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Select valid roles."))
        return redirect("manage:user_detail", public_id=account.public_id)
    try:
        services.set_roles(actor=request.user, user=account, roles=form.cleaned_data["roles"])
    except DomainError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("The roles have been updated."))
    return redirect("manage:user_detail", public_id=account.public_id)


@manage_required
def user_import(request):
    if request.method == "POST":
        form = UserImportForm(request.POST, request.FILES)
    else:
        form = UserImportForm()
    if request.method == "POST" and form.is_valid():
        result = services.import_users_csv(actor=request.user, file=form.cleaned_data["file"])
        return render(request, "manage/user_import_result.html", {"result": result})
    return render(request, "manage/user_import.html", {"form": form})
