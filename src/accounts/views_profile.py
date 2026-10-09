"""Profile pages and personal preferences of the logged-in user."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods

from audit.services import record
from core.views_i18n import set_language_cookie

from .forms import PreferencesForm, ProfileForm
from .models import User
from .policies import can_view_profile, can_view_profile_details


def _own_profile_url(user) -> str:
    return reverse("accounts:profile_detail", args=[user.public_id])


@login_required
@require_GET
def profile_me(request):
    return redirect(_own_profile_url(request.user))


@login_required
@require_GET
def profile_detail(request, public_id):
    owner = get_object_or_404(
        User.objects.select_related("profile", "employment__unit"), public_id=public_id
    )
    if not can_view_profile(request.user, owner):
        raise Http404
    show_details = can_view_profile_details(request.user, owner)
    context = {
        "owner": owner,
        "profile": owner.profile,
        "is_self": owner.pk == request.user.pk,
        "show_details": show_details,
        "interests": list(owner.profile.interests.all()) if show_details else [],
    }
    return render(request, "accounts/profile_detail.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def profile_edit(request):
    profile = request.user.profile
    form = ProfileForm(request.POST if request.method == "POST" else None, instance=profile)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
            record(
                actor=request.user,
                action="profile.updated",
                target=request.user,
                changes={"fields": sorted(form.changed_data)},
            )
        messages.success(request, _("Your profile has been updated."))
        return redirect(_own_profile_url(request.user))
    return render(request, "accounts/profile_edit.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def preferences(request):
    profile = request.user.profile
    form = PreferencesForm(request.POST if request.method == "POST" else None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        language = form.cleaned_data["language"]
        if language:
            translation.activate(language)
            request.LANGUAGE_CODE = language
        messages.success(request, _("Your preferences have been saved."))
        response = redirect("accounts:preferences")
        if language:
            set_language_cookie(response, language)
        return response
    return render(request, "accounts/preferences.html", {"form": form})
