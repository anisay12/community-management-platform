import ast
import pathlib

import pytest

from api import exceptions

pytestmark = [pytest.mark.django_db, pytest.mark.urls("api.tests.urls_errors")]

SRC = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def logged(api_client, active_user):
    api_client.force_login(active_user)
    api_client.raise_request_exception = False  # let tests read the 500 body
    return api_client


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/api/t/conflict/", 409, "edit_conflict"),
        ("/api/t/forbidden/", 403, "read_only"),
        ("/api/t/bad/", 400, "title_required"),
        ("/api/t/unknown/", 400, "never_seen_code"),
        ("/api/t/storage/", 503, "storage_unavailable"),
        ("/api/t/rate/", 429, "rate_limited"),
        ("/api/t/404/", 404, "not_found"),
        ("/api/t/403/", 403, "permission_denied"),
        ("/api/t/invalid/", 400, "invalid"),
        ("/api/t/boom/", 500, "server_error"),
    ],
)
def test_error_shape_and_status(logged, path, status, code):
    response = logged.get(path)
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    assert body["detail"]
    assert body["request_id"] == response["X-Request-ID"]


def test_permission_error_codes(api_client, make_user, functional_admin):
    assert api_client.get("/api/v1/me/").json()["code"] == "not_authenticated"
    suspended = make_user("sus@example.com", status="suspended")
    api_client.force_login(suspended)
    assert api_client.get("/api/v1/me/").json()["code"] == "not_authenticated"
    api_client.force_login(functional_admin)
    assert api_client.get("/api/v1/me/").json()["code"] == "mfa_required"


def test_rate_limited_sets_retry_after(logged):
    assert logged.get("/api/t/rate/")["Retry-After"] == "120"


def test_not_found_does_not_echo_the_message(logged):
    assert "Post" not in logged.get("/api/t/404/").json()["detail"]


def test_validation_errors_are_listed_per_field(logged):
    assert logged.get("/api/t/invalid/").json()["fields"] == {"title": ["This field is required."]}


def test_server_error_hides_internals(logged):
    assert "secret internals" not in logged.get("/api/t/boom/").content.decode()


def test_server_error_reaches_error_collectors(logged):
    from django.core.signals import got_request_exception

    received = []

    def receiver(sender, request=None, **kwargs):
        received.append(request)

    got_request_exception.connect(receiver)
    try:
        logged.get("/api/t/boom/")
    finally:
        got_request_exception.disconnect(receiver)
    assert received


def test_server_error_reraises_in_debug(logged, settings):
    settings.DEBUG = True
    logged.raise_request_exception = True
    with pytest.raises(RuntimeError):
        logged.get("/api/t/boom/")


def test_error_detail_is_translated(logged, active_user):
    active_user.profile.language = "fr"
    active_user.profile.save()
    response = logged.get("/api/t/404/", HTTP_ACCEPT_LANGUAGE="fr")
    assert response.json()["detail"] != "Not found."


def _domain_error_codes() -> set[str]:
    codes = set()
    for file in SRC.rglob("*.py"):
        if "tests" in file.parts or "migrations" in file.parts:
            continue
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", None))
            first = node.args[0]
            if name == "DomainError" and isinstance(first, ast.Constant):
                codes.add(first.value)
    return codes


def test_every_domain_error_code_is_classified():
    classified = (
        exceptions.FORBIDDEN_CODES
        | exceptions.CONFLICT_CODES
        | exceptions.BAD_REQUEST_CODES
        | exceptions.UNAVAILABLE_CODES
        | {exceptions.RATE_LIMITED}
    )
    missing = _domain_error_codes() - classified
    assert not missing, f"classify these DomainError codes in api/exceptions.py: {missing}"
