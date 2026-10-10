"""HTTP helpers of the posts views: HTMX detection, and the translation of a ``DomainError``
raised by a posts service into an HTTP response."""

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render

from core.views_errors import too_many_requests


def is_htmx(request) -> bool:
    """Whether ``request`` comes from HTMX (it then expects a fragment, not a page)."""
    return bool(request.headers.get("HX-Request"))


def domain_error_response(request, error, *, redirect_to):
    """``rate_limited`` → 429 with ``Retry-After``; ``edit_conflict`` → 409 page with a link
    to ``redirect_to`` (views re-displaying a form build their own 409); other codes → error
    toast and redirect to ``redirect_to`` (``HX-Redirect`` for HTMX requests)."""
    if error.code == "rate_limited":
        response = too_many_requests(request)
        response["Retry-After"] = str(getattr(error, "retry_after", 3600))
        return response
    if error.code == "edit_conflict":
        return render(
            request,
            "posts/edit_conflict.html",
            {"message": error.message, "reload_url": redirect_to},
            status=409,
        )
    messages.error(request, error.message)
    if is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = redirect_to
        return response
    return redirect(redirect_to)
