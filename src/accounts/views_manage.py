"""Account administration pages, reserved to functional administrators and superusers."""

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from core.errors import DomainError

from . import privacy, services
from .forms import UserCreateForm, UserImportForm, UserRolesForm, UserStatusForm
from .models import User
from .policies import can_administer_user, can_manage_users
from .roles import user_roles
from .selectors import users_for_admin

PAGE_SIZE = 20
# The import result travels to the result page through the session (Post/Redirect/Get).
IMPORT_RESULT_SESSION_KEY = "manage_import_result"
MAX_STORED_IMPORT_ERRORS = 100

STATUS_ACTIONS = {
    "suspend": (services.suspend_user, gettext_lazy("The account has been suspended.")),
    "reactivate": (services.reactivate_user, gettext_lazy("The account has been reactivated.")),
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
    form = UserCreateForm(request.POST if request.method == "POST" else None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            account = services.create_user(
                actor=request.user,
                email=data["email"],
                first_name=data["first_name"],
                last_name=data["last_name"],
                unit=data["unit"],
                manager=data["manager"],
                roles=data["roles"],
            )
        except DomainError as error:
            form.add_error(form.PROBLEM_FIELDS.get(error.code), error.message)
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
        "can_administer": can_administer_user(request.user, account),
        "roles_form": UserRolesForm(
            initial={"roles": sorted(user_roles(account))}, actor=request.user
        ),
    }
    return render(request, "manage/user_detail.html", context)


@manage_required
@require_POST
def user_status(request, public_id):
    account = _account(public_id)
    if not can_administer_user(request.user, account):
        raise PermissionDenied
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
    if not can_administer_user(request.user, account):
        raise PermissionDenied
    form = UserRolesForm(request.POST, actor=request.user)
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
@require_http_methods(["GET", "POST"])
def user_deactivate_confirm(request, public_id):
    """Deactivation cannot be undone in the application: confirm it on its own page."""
    account = _account(public_id)
    if not can_administer_user(request.user, account):
        raise PermissionDenied
    if account.status == User.Status.DEACTIVATED:
        messages.error(request, _("This account is already deactivated."))
        return redirect("manage:user_detail", public_id=account.public_id)
    if request.method == "POST":
        try:
            services.deactivate_user(actor=request.user, user=account)
        except DomainError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, _("The account has been deactivated."))
        return redirect("manage:user_detail", public_id=account.public_id)
    return render(request, "manage/user_deactivate_confirm.html", {"account": account})


@manage_required
@require_http_methods(["GET", "POST"])
def user_anonymize(request, public_id):
    """Anonymization erases personal data for good: confirm it on its own page."""
    account = _account(public_id)
    if not can_administer_user(request.user, account):
        raise PermissionDenied
    if account.anonymized_at is not None:
        messages.error(request, _("This account is already anonymized."))
        return redirect("manage:user_detail", public_id=account.public_id)
    if request.method == "POST":
        try:
            privacy.anonymize_user(actor=request.user, user=account)
        except DomainError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, _("The account has been anonymized."))
        return redirect("manage:user_detail", public_id=account.public_id)
    return render(
        request,
        "manage/user_anonymize_confirm.html",
        {
            "account": account,
            "can_anonymize": privacy.can_be_anonymized(account),
            "status_message": privacy.ANONYMIZE_STATUS_MESSAGE,
        },
    )


@manage_required
def user_import(request):
    if request.method == "POST":
        form = UserImportForm(request.POST, request.FILES)
    else:
        form = UserImportForm()
    if request.method == "POST" and form.is_valid():
        result = services.import_users_csv(actor=request.user, file=form.cleaned_data["file"])
        request.session[IMPORT_RESULT_SESSION_KEY] = {
            "created": result.created,
            "error_count": len(result.errors),
            "errors": [
                [error.line, str(error.message)]
                for error in result.errors[:MAX_STORED_IMPORT_ERRORS]
            ],
        }
        return redirect("manage:user_import_result")
    return render(request, "manage/user_import.html", {"form": form})


@manage_required
def user_import_result(request):
    summary = request.session.pop(IMPORT_RESULT_SESSION_KEY, None)
    if summary is None:
        return redirect("manage:user_import")
    errors = [{"line": line, "message": message} for line, message in summary["errors"]]
    context = {
        "created": summary["created"],
        "errors": errors,
        "hidden_error_count": summary["error_count"] - len(errors),
    }
    return render(request, "manage/user_import_result.html", context)
