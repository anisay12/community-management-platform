import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient
from rest_framework.views import APIView

from api.authentication import SessionAuthentication
from api.permissions import IsActiveVerifiedUser

pytestmark = pytest.mark.django_db


def _walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def _api_routes():
    return [
        (route, view)
        for route, view in _walk(get_resolver().url_patterns)
        if route.startswith("api/")
    ]


def test_api_routes_exist():
    assert _api_routes()


@pytest.mark.parametrize(("route", "view"), _api_routes())
def test_every_api_route_is_a_guarded_drf_view(route, view):
    view_class = getattr(view, "cls", None)
    assert isinstance(view_class, type) and issubclass(view_class, APIView), route
    assert any(issubclass(p, IsActiveVerifiedUser) for p in view_class.permission_classes), route
    assert view_class.authentication_classes, route
    assert all(issubclass(a, SessionAuthentication) for a in view_class.authentication_classes), (
        route
    )


def test_no_cors_header(api_client, active_user):
    api_client.force_login(active_user)
    response = api_client.get("/api/v1/me/", HTTP_ORIGIN="https://evil.example.com")
    assert not response.has_header("Access-Control-Allow-Origin")


def test_csrf_enforced_on_writes(active_user):
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(active_user)
    response = client.post("/api/v1/me/", {}, format="json")
    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]
