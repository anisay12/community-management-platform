"""Profile pages, profile photo and personal preferences of the logged-in user."""

from types import SimpleNamespace

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from audit.services import record
from core.errors import DomainError
from core.i18n import delete_language_cookie, set_language_cookie
from documents.downloads import protected_location

from . import services
from .avatars import OUTPUT_MIME_TYPE
from .forms import AvatarForm, PreferencesForm, ProfileForm
from .models import User
from .policies import can_view_profile, can_view_profile_details
from .roles import Role, user_roles


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
    is_self = owner.pk == request.user.pk
    context = {
        "owner": owner,
        "profile": owner.profile,
        "is_self": is_self,
        "is_active": owner.status == User.Status.ACTIVE,
        "show_details": show_details,
        "interests": list(owner.profile.interests.all()) if show_details else [],
        # Roles are only shown to their holder.
        "roles": [r.value for r in Role if r.value in user_roles(owner)] if is_self else [],
    }
    return render(request, "accounts/profile_detail.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def profile_edit(request):
    profile = request.user.profile
    form = ProfileForm(request.POST if request.method == "POST" else None, instance=profile)
    if request.method == "POST" and form.is_valid():
        if form.changed_data:
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
    return render(
        request,
        "accounts/profile_edit.html",
        {"form": form, "avatar_form": AvatarForm(), "profile": profile},
    )


@login_required
@require_POST
def avatar_upload(request):
    """Queue a new profile photo for its virus scan; errors come back as a toast."""
    form = AvatarForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, _("Choose a photo to upload."))
        return redirect("accounts:profile_edit")
    try:
        services.upload_avatar(actor=request.user, upload=form.cleaned_data["photo"])
    except DomainError as error:
        messages.error(request, error.message)
    else:
        messages.success(
            request,
            _("Your photo is being checked by the antivirus. It will appear in a moment."),
        )
    return redirect("accounts:profile_edit")


@login_required
@require_POST
def avatar_remove(request):
    services.remove_avatar(actor=request.user)
    messages.success(request, _("Your photo has been removed."))
    return redirect("accounts:profile_edit")


# The avatar URL carries a ``?v=`` token that changes with every new photo, so a short
# private browser cache is safe: it saves a request per page for menus and lists, and an
# access change (e.g. a deactivated owner) reaches a viewer within these few minutes.
AVATAR_CSP = "sandbox; default-src 'none'"


@login_required
@require_GET
def avatar(request, public_id):
    """Serve a profile photo (clean, re-encoded WebP) through Nginx ``X-Accel-Redirect``.

    404 unless the viewer may see the profile and a photo exists. With ``DEBUG`` and
    ``DOCUMENT_DEV_STREAMING`` Django streams it itself, as for documents.
    """
    owner = get_object_or_404(User.objects.select_related("profile"), public_id=public_id)
    if not can_view_profile(request.user, owner) or not owner.profile.avatar:
        raise Http404
    key = owner.profile.avatar.name
    if settings.DEBUG and settings.DOCUMENT_DEV_STREAMING:
        response = FileResponse(default_storage.open(key, "rb"), content_type=OUTPUT_MIME_TYPE)
    else:
        response = HttpResponse(content=b"", content_type=OUTPUT_MIME_TYPE)
        response["X-Accel-Redirect"] = protected_location(SimpleNamespace(storage_key=key))
    response["Content-Type"] = OUTPUT_MIME_TYPE
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = f"private, max-age={settings.AVATAR_CACHE_SECONDS}"
    response["Content-Security-Policy"] = AVATAR_CSP
    return response


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
        else:
            delete_language_cookie(response)
        return response
    return render(request, "accounts/preferences.html", {"form": form})
