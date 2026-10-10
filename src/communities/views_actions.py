"""Member actions (join, leave, requests, invitations) and community creation.

Actions are POST-only and redirect back (PRG) with a toast; a refused business rule
(``DomainError``) becomes an error toast or a form error, never a server error.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from core.errors import DomainError

from . import policies, services
from .forms_actions import CommunityCreateForm, CreationRequestForm, JoinRequestForm
from .models import Community, CommunityInvitation, MembershipRequest
from .selectors import visible_communities

INVITATIONS_LIMIT = 50


def _visible(request, slug):
    return get_object_or_404(visible_communities(request.user), slug=slug)


def _back(community):
    return redirect("communities:detail", slug=community.slug)


def _back_to_next(request, community):
    """Redirect to the ``next`` field when it is a safe URL of this site (e.g. the post whose
    join prompt was used), else to the community."""
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return _back(community)


@login_required
@require_POST
def join(request, slug):
    """Join (or ask to join) the community; back to ``next`` when it is a safe local URL."""
    community = _visible(request, slug)
    form = JoinRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Your message is too long (500 characters at most)."))
        return _back_to_next(request, community)
    try:
        result = services.join(
            actor=request.user, community=community, message=form.cleaned_data["message"]
        )
    except DomainError as error:
        messages.error(request, error.message)
    else:
        if isinstance(result, MembershipRequest):
            messages.success(request, _("Your request to join has been sent."))
        else:
            messages.success(request, _("Welcome! You are now a member."))
    return _back_to_next(request, community)


@login_required
@require_POST
def leave(request, slug):
    community = _visible(request, slug)
    try:
        services.leave(actor=request.user, community=community)
    except DomainError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("You have left the community."))
    return _back(community)


@login_required
@require_POST
def cancel_request(request, slug):
    community = _visible(request, slug)
    pending = MembershipRequest.objects.filter(
        community=community, user=request.user, status=MembershipRequest.Status.PENDING
    ).first()
    if pending is None:
        messages.error(request, _("You have no pending request for this community."))
        return _back(community)
    try:
        services.cancel_request(actor=request.user, request=pending)
    except DomainError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, _("Your request has been cancelled."))
    return _back(community)


@login_required
@require_safe
def my_invitations(request):
    invitations = (
        CommunityInvitation.objects.filter(
            invited_user=request.user,
            status=CommunityInvitation.Status.PENDING,
            expires_at__gt=timezone.now(),
            community__status=Community.Status.ACTIVE,
        )
        .select_related("community", "community__category", "invited_by")
        .order_by("-created_at")[:INVITATIONS_LIMIT]
    )
    return render(request, "communities/my_invitations.html", {"invitations": invitations})


@login_required
@require_POST
def invitation_respond(request, public_id):
    invitation = get_object_or_404(
        CommunityInvitation.objects.select_related("community"),
        public_id=public_id,
        invited_user=request.user,
    )
    decision = request.POST.get("decision")
    if decision not in ("accept", "decline"):
        return HttpResponseBadRequest()
    try:
        if decision == "accept":
            services.accept_invitation(actor=request.user, invitation=invitation)
        else:
            services.decline_invitation(actor=request.user, invitation=invitation)
    except DomainError as error:
        messages.error(request, error.message)
        return redirect("communities:my_invitations")
    if decision == "decline":
        messages.success(request, _("Invitation declined."))
        return redirect("communities:my_invitations")
    messages.success(request, _("Welcome! You are now a member."))
    return _back(invitation.community)


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def create(request):
    if not policies.can_create_community(request.user):
        return redirect("communities:creation_request")
    form = CommunityCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            community = services.create_community(actor=request.user, **form.cleaned_data)
        except DomainError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, _("The community has been created."))
            return _back(community)
    return render(request, "communities/create.html", {"form": form})


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def creation_request(request):
    if policies.can_create_community(request.user):
        return redirect("communities:create")
    form = CreationRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.request_creation(actor=request.user, **form.cleaned_data)
        except DomainError as error:
            form.add_error(None, error.message)
        else:
            messages.success(
                request, _("Your request has been sent to the administrators for review.")
            )
            return redirect("communities:catalogue")
    return render(request, "communities/creation_request.html", {"form": form})
