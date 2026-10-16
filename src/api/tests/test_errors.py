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
        ("/api/t/badrequest/", 400, "bad_request"),
        ("/api/t/suspicious/", 400, "bad_request"),
        ("/api/t/throttled/", 429, "rate_limited"),
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


def test_server_error_reraises_in_debug(settings):
    from rest_framework.test import APIRequestFactory

    context = {"request": APIRequestFactory().get("/api/t/boom/"), "view": None}
    settings.DEBUG = True
    assert exceptions.exception_handler(RuntimeError("x"), context) is None
    settings.DEBUG = False
    response = exceptions.exception_handler(RuntimeError("x"), context)
    assert response.status_code == 500


def test_nested_serializer_errors_are_flattened(logged):
    assert logged.get("/api/t/nested/").json()["fields"] == {"author.email": ["Bad email."]}
    assert logged.get("/api/t/listed/").json()["fields"] == {"items.1.title": ["Required."]}
    assert logged.get("/api/t/toplist/").json()["fields"] == {"non_field_errors": ["Nope."]}


@pytest.mark.parametrize("path", ["/api/t/badrequest/", "/api/t/suspicious/"])
def test_django_400s_stay_400_and_are_not_unhandled(logged, path):
    from django.core.signals import got_request_exception

    received = []

    def receiver(sender, **kwargs):
        received.append(1)

    got_request_exception.connect(receiver)
    try:
        response = logged.get(path)
    finally:
        got_request_exception.disconnect(receiver)
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"
    assert "internal detail" not in response.content.decode()
    assert not received


def test_throttled_without_wait_has_retry_after(logged):
    assert logged.get("/api/t/throttled/")["Retry-After"] == "3600"


def test_code_sets_are_disjoint():
    sets = [
        exceptions.FORBIDDEN_CODES,
        exceptions.CONFLICT_CODES,
        exceptions.UNAVAILABLE_CODES,
        exceptions.BAD_REQUEST_CODES,
        {exceptions.RATE_LIMITED},
    ]
    for i, a in enumerate(sets):
        for b in sets[i + 1 :]:
            assert not (a & b), a & b


def test_error_detail_is_translated(logged, active_user):
    active_user.profile.language = "fr"
    active_user.profile.save()
    response = logged.get("/api/t/404/", HTTP_ACCEPT_LANGUAGE="fr")
    assert response.json()["detail"] != "Not found."


DYNAMIC_DOMAIN_ERROR_SITES = {"accounts/services.py:create_user"}


def _scan_domain_errors() -> tuple[set[str], set[str]]:
    codes, dynamic = set(), set()
    for file in SRC.rglob("*.py"):
        if "tests" in file.parts or "migrations" in file.parts:
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", getattr(node.func, "attr", None)) != "DomainError":
                continue
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant):
                codes.add(first.value)
                continue
            scope = node
            while scope in parents and not isinstance(
                scope, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                scope = parents[scope]
            name = getattr(scope, "name", "<module>")
            dynamic.add(f"{file.relative_to(SRC).as_posix()}:{name}")
    return codes, dynamic


def _domain_error_codes() -> set[str]:
    return _scan_domain_errors()[0]


def _classified() -> set[str]:
    return (
        exceptions.FORBIDDEN_CODES
        | exceptions.CONFLICT_CODES
        | exceptions.BAD_REQUEST_CODES
        | exceptions.UNAVAILABLE_CODES
        | {exceptions.RATE_LIMITED}
    )


def test_dynamic_domain_error_sites_are_known():
    assert _scan_domain_errors()[1] == DYNAMIC_DOMAIN_ERROR_SITES


def test_new_user_validation_codes_are_classified(make_user):
    from accounts.services import validate_new_user_fields

    make_user("taken@example.com")
    long = "x" * 151
    problems = (
        validate_new_user_fields(email="not-an-email")
        + validate_new_user_fields(email="taken@example.com")
        + validate_new_user_fields(email="a@example.com", first_name=" ", last_name=" ")
        + validate_new_user_fields(email="a@example.com", first_name=long, last_name=long)
        + validate_new_user_fields(email="a@example.com", manager_email="a@example.com")
        + validate_new_user_fields(email="a@example.com", manager_email="ghost@example.com")
    )
    codes = {code for code, _message in problems}
    assert codes == {
        "invalid_email",
        "email_taken",
        "first_name_required",
        "last_name_required",
        "first_name_too_long",
        "last_name_too_long",
        "manager_self",
        "manager_requires_unit",
        "unknown_manager",
    }
    assert codes <= _classified()
    assert exceptions.status_for("email_taken") == 409


def test_every_domain_error_code_is_classified():
    missing = _domain_error_codes() - _classified()
    assert not missing, f"classify these DomainError codes in api/exceptions.py: {missing}"


def _security_records(caplog):
    return [r for r in caplog.records if r.name.startswith("django.security.")]


def test_suspicious_operation_keeps_django_security_log(logged, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    response = logged.get("/api/t/suspicious/")
    assert response.status_code == 400
    assert "internal detail" not in response.content.decode()
    records = [
        r for r in _security_records(caplog) if r.name == "django.security.SuspiciousOperation"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.ERROR
    assert record.exc_info[1].args == ("internal detail",)
    assert record.status_code == 400
    assert record.request.path == "/api/t/suspicious/"


def test_plain_bad_request_has_no_security_log(logged, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    assert logged.get("/api/t/badrequest/").status_code == 400
    assert _security_records(caplog) == []
