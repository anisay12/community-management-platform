from html import escape
from pathlib import Path

from django.http import HttpRequest, HttpResponse, HttpResponseServerError
from django.shortcuts import render
from django.utils.translation import get_language
from django.utils.translation import gettext as _

_STATIC_500 = (Path(__file__).resolve().parents[1] / "templates" / "500.html").read_text(
    encoding="utf-8"
)


def page_not_found(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    return render(request, "errors/404.html", status=404)


def permission_denied(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    return render(request, "errors/403.html", status=403)


def too_many_requests(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    return render(request, "errors/429.html", status=429)


def server_error(request: HttpRequest) -> HttpResponse:
    """Render the 500 page from a static document: no template engine, no database access."""
    request_id = getattr(request, "request_id", "")
    reference = ""
    if request_id:
        reference = "<p>{} <code>{}</code></p>".format(
            escape(_("Reference")), escape(str(request_id))
        )
    replacements = {
        "@@LANG@@": escape(get_language() or "en"),
        "@@TITLE@@": escape(_("Server error")),
        "@@SITE@@": escape(_("Communities")),
        "@@MESSAGE@@": escape(_("Something went wrong on our side. Please try again later.")),
        "@@REFERENCE@@": reference,
    }
    body = _STATIC_500
    for placeholder, value in replacements.items():
        body = body.replace(placeholder, value)
    return HttpResponseServerError(body)
