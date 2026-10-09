"""Personal data pages of the logged-in user: request and download a copy of their data."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from . import services
from .models import DataExport


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def data_export(request):
    if request.method == "POST":
        services.request_data_export(user=request.user)
        messages.success(
            request, _("Your data is being prepared. Come back to this page in a few minutes.")
        )
        return redirect("accounts:data_export")
    export = DataExport.objects.filter(user=request.user).first()
    context = {
        "export": export,
        "downloadable": export is not None and export.is_downloadable(),
        "can_request": export is None
        or export.status == DataExport.Status.FAILED
        or (export.status == DataExport.Status.READY and not export.is_downloadable()),
    }
    return render(request, "accounts/data_export.html", context)


@login_required
@never_cache
@require_GET
def data_export_download(request, public_id):
    """Only the owner may download, and only a ready export that has not expired."""
    export = get_object_or_404(DataExport, public_id=public_id, user=request.user)
    if not export.is_downloadable():
        raise Http404
    # Streamed by Django until L5 serves private files through X-Accel-Redirect (ADR-0001).
    return FileResponse(export.file.open("rb"), as_attachment=True, filename="my-data.json")
