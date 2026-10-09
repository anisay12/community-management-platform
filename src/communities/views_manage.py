"""Community management pages: settings, members, requests, invitations and status.

A community the user may not see answers 404; a visible one the user may not manage answers
403. The services enforce every rule again; their ``DomainError`` becomes a message or a form
error, never a server error.
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from core.errors import DomainError

from . import policies, services
from .forms_manage import (
    CommunitySettingsForm,
    DecisionForm,
    InvitationForm,
    MemberSearchForm,
    RoleForm,
    StatusForm,
)
from .models import Community, CommunityInvitation, CommunityMembership, MembershipRequest
from .roles import CommunityRole, role_at_least
from .selectors import visible_communities

MEMBERS_PAGE_SIZE = 50
LIST_LIMIT = 100

# action -> (service, policy, statuses it starts from, success message, confirmation text)
STATUS_ACTIONS = {
    "suspend": (
        services.suspend,
        policies.can_suspend,
        {Community.Status.ACTIVE},
        gettext_lazy("The community has been suspended."),
        gettext_lazy("Suspend the community"),
        gettext_lazy("Members keep read access but nothing can be changed until reactivation."),
    ),
    "reactivate": (
        services.reactivate,
        policies.can_suspend,
        {Community.Status.SUSPENDED},
        gettext_lazy("The community has been reactivated."),
        gettext_lazy("Reactivate the community"),
        gettext_lazy("The community becomes fully usable again."),
    ),
    "archive": (
        services.archive,
        policies.can_archive,
        {Community.Status.ACTIVE, Community.Status.SUSPENDED},
        gettext_lazy("The community has been archived."),
        gettext_lazy("Archive the community"),
        gettext_lazy(
            "The community leaves the catalogue and becomes read-only; only its members "
            "and functional administrators can still see it."
        ),
    ),
    "unarchive": (
        services.unarchive,
        policies.can_archive,
        {Community.Status.ARCHIVED},
        gettext_lazy("The community has been restored."),
        gettext_lazy("Restore the community"),
        gettext_lazy("The community becomes active again and returns to the catalogue."),
    ),
}


def _community(request, slug) -> Community:
    return get_object_or_404(visible_communities(request.user), slug=slug)


def manage_view(policy):
    """Resolve the visible community (404) and require ``policy`` on it (403)."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, slug, *args, **kwargs):
            community = _community(request, slug)
            if not policy(request.user, community):
                raise PermissionDenied
            return view(request, community, *args, **kwargs)

        return login_required(never_cache(wrapped))

    return decorator


def _read_only(user, community) -> bool:
    """Suspended and archived communities only change through functional administrators."""
    return community.status != Community.Status.ACTIVE and not policies.is_functional_admin(user)


def _manage_tabs(user, community, current):
    entries = [
        ("settings", _("Settings"), "communities:manage_settings", policies.can_configure),
        ("members", _("Members"), "communities:manage_members", policies.can_manage_members),
        ("requests", _("Requests"), "communities:manage_requests", policies.can_manage_members),
        (
            "invitations",
            _("Invitations"),
            "communities:manage_invitations",
            policies.can_manage_members,
        ),
    ]
    return [
        (label, reverse(name, args=[community.slug]), key == current)
        for key, label, name, allowed in entries
        if allowed(user, community)
    ]


def _context(request, community, current, **extra):
    user = request.user
    context = {
        "community": community,
        "manage_tabs": _manage_tabs(user, community, current),
        "read_only": _read_only(user, community),
    }
    context.update(extra)
    return context


def _status_actions(user, community):
    return [
        {"action": name, "title": spec[4], "body": spec[5]}
        for name, spec in STATUS_ACTIONS.items()
        if community.status in spec[2] and spec[1](user, community)
    ]


# --- settings --------------------------------------------------------------------------------


@manage_view(policies.can_configure)
@require_http_methods(["GET", "HEAD", "POST"])
def settings(request, community):
    read_only = _read_only(request.user, community)
    form = CommunitySettingsForm(
        request.POST or None, instance=Community.objects.get(pk=community.pk)
    )
    if request.method == "POST":
        if read_only:
            form.add_error(
                None, _("This community is suspended or archived: it cannot be changed.")
            )
        elif form.is_valid():
            try:
                services.update_settings(
                    actor=request.user, community=community, **form.cleaned_data
                )
            except DomainError as error:
                field = "name" if error.code == "name_taken" else None
                form.add_error(field, error.message)
            else:
                messages.success(request, _("The settings have been saved."))
                return redirect("communities:manage_settings", slug=community.slug)
    context = _context(
        request,
        community,
        "settings",
        form=form,
        status_actions=_status_actions(request.user, community),
    )
    return render(request, "communities/manage_settings.html", context)


# --- members ---------------------------------------------------------------------------------


@manage_view(policies.can_manage_members)
@require_http_methods(["GET", "HEAD"])
def members(request, community):
    user = request.user
    form = MemberSearchForm(request.GET)
    memberships = community.memberships.select_related("user").order_by(
        "user__first_name", "user__last_name", "pk"
    )
    query = form.cleaned_data.get("q") if form.is_valid() else ""
    if query:
        memberships = memberships.filter(
            Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query)
        )
    can_change_roles = policies.can_change_roles(user, community)
    page_obj = Paginator(memberships, MEMBERS_PAGE_SIZE).get_page(request.GET.get("page"))
    for membership in page_obj:
        membership.removable = membership.user_id != user.pk and (
            can_change_roles or not role_at_least(membership.role, CommunityRole.ANIMATOR)
        )
    context = _context(
        request,
        community,
        "members",
        form=form,
        page_obj=page_obj,
        can_change_roles=can_change_roles,
        role_choices=CommunityRole.choices,
    )
    return render(request, "communities/manage_members.html", context)


def _membership(community, user_public_id) -> CommunityMembership:
    return get_object_or_404(
        CommunityMembership.objects.select_related("user", "community"),
        community=community,
        user__public_id=user_public_id,
    )


@manage_view(policies.can_change_roles)
@require_POST
def member_role(request, community, user_public_id):
    membership = _membership(community, user_public_id)
    form = RoleForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Choose a valid role."))
    else:
        try:
            services.change_role(
                actor=request.user, membership=membership, role=form.cleaned_data["role"]
            )
        except DomainError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, _("The role has been changed."))
    return redirect("communities:manage_members", slug=community.slug)


@manage_view(policies.can_manage_members)
@require_POST
def member_remove(request, community, user_public_id):
    membership = _membership(community, user_public_id)
    try:
        services.remove_member(actor=request.user, membership=membership)
    except DomainError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("The member has been removed."))
    return redirect("communities:manage_members", slug=community.slug)


# --- membership requests ---------------------------------------------------------------------


@manage_view(policies.can_manage_members)
@require_http_methods(["GET", "HEAD"])
def requests(request, community):
    pending_first = Case(
        When(status=MembershipRequest.Status.PENDING, then=Value(0)),
        default=Value(1),
        output_field=IntegerField(),
    )
    found = (
        community.membership_requests.select_related("user")
        .annotate(pending_first=pending_first)
        .order_by("pending_first", "-created_at")[:LIST_LIMIT]
    )
    context = _context(request, community, "requests", requests=found)
    return render(request, "communities/manage_requests.html", context)


@manage_view(policies.can_manage_members)
@require_POST
def request_decide(request, community, public_id):
    membership_request = get_object_or_404(
        MembershipRequest.objects.select_related("user", "community"),
        community=community,
        public_id=public_id,
    )
    form = DecisionForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest()
    try:
        if membership_request.status != MembershipRequest.Status.PENDING:
            raise DomainError("invalid_state", _("This request has already been decided."))
        if form.cleaned_data["decision"] == "accept":
            services.accept_request(actor=request.user, request=membership_request)
            messages.success(request, _("The request has been accepted."))
        else:
            services.reject_request(
                actor=request.user, request=membership_request, note=form.cleaned_data["note"]
            )
            messages.success(request, _("The request has been rejected."))
    except DomainError as error:
        messages.error(request, error.message)
    return redirect("communities:manage_requests", slug=community.slug)


# --- invitations -----------------------------------------------------------------------------


@manage_view(policies.can_manage_members)
@require_http_methods(["GET", "HEAD", "POST"])
def invitations(request, community):
    can_change_roles = policies.can_change_roles(request.user, community)
    form = InvitationForm(request.POST or None, can_change_roles=can_change_roles)
    if request.method == "POST" and form.is_valid():
        try:
            services.invite(
                actor=request.user,
                community=community,
                user=form.invited_user,
                role=form.cleaned_data["role"],
            )
        except DomainError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, _("The invitation has been sent."))
            return redirect("communities:manage_invitations", slug=community.slug)
    found = (
        CommunityInvitation.objects.filter(community=community)
        .select_related("invited_user", "invited_by")
        .order_by("-created_at")[:LIST_LIMIT]
    )
    context = _context(request, community, "invitations", form=form, invitations=found)
    return render(request, "communities/manage_invitations.html", context)


@manage_view(policies.can_manage_members)
@require_POST
def invitation_revoke(request, community, public_id):
    invitation = get_object_or_404(
        CommunityInvitation.objects.select_related("community"),
        community=community,
        public_id=public_id,
    )
    try:
        services.revoke_invitation(actor=request.user, invitation=invitation)
    except DomainError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("The invitation has been revoked."))
    return redirect("communities:manage_invitations", slug=community.slug)


# --- status ----------------------------------------------------------------------------------


def _may_change_status(user, community) -> bool:
    return policies.can_suspend(user, community) or policies.can_archive(user, community)


@manage_view(_may_change_status)
@require_http_methods(["GET", "HEAD", "POST"])
def status(request, community):
    """POST runs a status action; GET is the confirmation page used without JavaScript."""
    form = StatusForm(request.POST if request.method == "POST" else request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest()
    action = form.cleaned_data["action"]
    service, policy, _sources, success, title, body = STATUS_ACTIONS[action]
    if not policy(request.user, community):
        raise PermissionDenied
    if request.method != "POST":
        context = _context(request, community, "settings", action=action, title=title, body=body)
        return render(request, "communities/manage_status_confirm.html", context)
    try:
        service(actor=request.user, community=community)
    except DomainError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, success)
    community.refresh_from_db(fields=["status"])
    if community.status == Community.Status.ARCHIVED and not policies.can_view_metadata(
        request.user, community
    ):
        return redirect("communities:catalogue")
    if policies.can_configure(request.user, community):
        return redirect("communities:manage_settings", slug=community.slug)
    return redirect("communities:detail", slug=community.slug)
