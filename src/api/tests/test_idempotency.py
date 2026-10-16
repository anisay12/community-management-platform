import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from redis import exceptions as redis_exceptions

from api.tests import urls_idempotency
from core import idempotency

pytestmark = [pytest.mark.django_db, pytest.mark.urls("api.tests.urls_idempotency")]


@pytest.fixture(autouse=True)
def _reset_calls():
    urls_idempotency.CALLS.clear()


@pytest.fixture
def logged(api_client, active_user):
    api_client.force_login(active_user)
    api_client.raise_request_exception = False  # let tests read the 500 body
    return api_client


def _post(client, path, key, body=None):
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key is not None else {}
    return client.post(path, body or {"title": "a"}, format="json", **headers)


def test_replay_runs_once_and_keeps_status(logged):
    key = str(uuid.uuid4())
    first = _post(logged, "/api/t/create/", key)
    second = _post(logged, "/api/t/create/", key)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(urls_idempotency.CALLS) == 1
    assert second["Idempotent-Replayed"] == "true"
    assert not first.has_header("Idempotent-Replayed")


def test_key_reused_with_other_body_is_refused(logged):
    key = str(uuid.uuid4())
    _post(logged, "/api/t/create/", key, {"title": "a"})
    response = _post(logged, "/api/t/create/", key, {"title": "b"})
    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_key_reused"


def test_required_key_missing(logged):
    response = _post(logged, "/api/t/create-required/", None)
    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_required"


def test_key_must_be_a_uuid(logged):
    assert _post(logged, "/api/t/create/", "not-a-uuid").status_code == 400


def test_optional_key_absent_runs_every_time(logged):
    _post(logged, "/api/t/create/", None)
    _post(logged, "/api/t/create/", None)
    assert len(urls_idempotency.CALLS) == 2


@pytest.mark.parametrize(
    ("method", "error"),
    [
        ("get", ConnectionError("redis down")),
        ("get", redis_exceptions.ConnectionError("redis down")),
        ("add", ConnectionError("redis down")),
    ],
    ids=["get-builtin", "get-redis", "add-builtin"],
)
def test_cache_down_fails_closed(logged, monkeypatch, method, error):
    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(idempotency.cache, method, broken)
    response = _post(logged, "/api/t/create/", str(uuid.uuid4()))
    assert response.status_code == 503
    assert response.json()["code"] == "idempotency_unavailable"
    assert urls_idempotency.CALLS == []


@pytest.mark.parametrize("body", [[{"a": 1}], ["x"], 5], ids=["list-of-objects", "list", "number"])
def test_non_object_json_body_is_fingerprinted(logged, body):
    key = str(uuid.uuid4())
    first = logged.post("/api/t/create/", body, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert first.status_code == 201
    again = logged.post("/api/t/create/", body, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert again.status_code == 201
    assert again["Idempotent-Replayed"] == "true"
    other = logged.post("/api/t/create/", ["different"], format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert other.status_code == 409
    assert other.json()["code"] == "idempotency_key_reused"


def test_every_file_under_one_field_is_fingerprinted(logged):
    key = str(uuid.uuid4())

    def post(first_name):
        files = [
            SimpleUploadedFile(first_name, b"aa"),
            SimpleUploadedFile("second.txt", b"bb"),
        ]
        return logged.post(
            "/api/t/create/", {"doc": files}, format="multipart", HTTP_IDEMPOTENCY_KEY=key
        )

    assert post("one.txt").status_code == 201
    response = post("other.txt")
    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_key_reused"


def test_write_failure_is_a_server_error_not_unavailable(logged):
    response = _post(logged, "/api/t/create-failing/", str(uuid.uuid4()))
    assert response.status_code == 500
    assert response.json()["code"] == "server_error"


def test_ui_stored_result_is_not_replayed_as_api(logged, active_user):
    key = str(uuid.uuid4())
    idempotency.run_once(active_user, "test:create", key, lambda: "ui-public-id")
    response = _post(logged, "/api/t/create/", key)
    assert response.status_code == 409
    assert urls_idempotency.CALLS == []
