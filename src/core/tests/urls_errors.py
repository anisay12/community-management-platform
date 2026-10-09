from django.core.exceptions import PermissionDenied
from django.urls import include, path

from core.views_errors import too_many_requests


def _forbidden(request):
    raise PermissionDenied


def _too_many(request):
    return too_many_requests(request)


urlpatterns = [
    path("forbidden/", _forbidden),
    path("too-many/", _too_many),
    path("", include("config.urls")),
]
handler403 = "core.views_errors.permission_denied"
handler404 = "core.views_errors.page_not_found"
handler500 = "core.views_errors.server_error"
