from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.urls import include, path
from rest_framework import serializers
from rest_framework.views import APIView

from core.errors import DomainError


def _raising(exc_factory):
    class _View(APIView):
        def get(self, request):
            raise exc_factory()

    return _View.as_view()


def _rate_limited():
    error = DomainError("rate_limited", "Too often")
    error.retry_after = 120
    return error


urlpatterns = [
    path("api/t/conflict/", _raising(lambda: DomainError("edit_conflict", "Changed"))),
    path("api/t/forbidden/", _raising(lambda: DomainError("read_only", "Read only"))),
    path("api/t/bad/", _raising(lambda: DomainError("title_required", "Title"))),
    path("api/t/unknown/", _raising(lambda: DomainError("never_seen_code", "Huh"))),
    path("api/t/storage/", _raising(lambda: DomainError("storage_unavailable", "Down"))),
    path("api/t/rate/", _raising(_rate_limited)),
    path("api/t/404/", _raising(lambda: Http404("No Post matches the given query."))),
    path("api/t/403/", _raising(PermissionDenied)),
    path(
        "api/t/invalid/",
        _raising(lambda: serializers.ValidationError({"title": ["This field is required."]})),
    ),
    path("api/t/boom/", _raising(lambda: RuntimeError("secret internals"))),
    path("", include("config.urls")),
]
