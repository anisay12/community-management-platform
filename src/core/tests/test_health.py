import pytest

from core import views


@pytest.mark.django_db
def test_healthz_returns_ok_without_database(client, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readyz_ok_when_dependencies_answer(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": True, "redis": True}}


@pytest.mark.django_db
def test_readyz_503_when_redis_down(client, monkeypatch):
    monkeypatch.setattr(views, "_check_redis", lambda: False)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": True, "redis": False},
    }


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
def test_probes_reject_post(client, path):
    assert client.post(path).status_code == 405


@pytest.mark.django_db
def test_database_check_reports_failure(monkeypatch):
    from django.db import DatabaseError

    def broken_cursor():
        raise DatabaseError("connexion perdue")

    monkeypatch.setattr(views.connection, "cursor", broken_cursor)
    assert views._check_database() is False


def test_redis_check_reports_unreachable_server(settings):
    settings.REDIS_URL = "redis://127.0.0.1:1/0"
    assert views._check_redis() is False


@pytest.mark.django_db
def test_readyz_does_not_leak_error_details(client, monkeypatch):
    monkeypatch.setattr(views, "_check_database", lambda: False)
    body = client.get("/readyz").content.decode()
    assert body == '{"status": "unavailable", "checks": {"database": false, "redis": true}}'
