import pytest
from django.conf import settings

from api.throttling import WriteThrottle

pytestmark = [pytest.mark.django_db, pytest.mark.urls("api.tests.urls_plumbing")]


@pytest.fixture
def logged(api_client, active_user):
    api_client.force_login(active_user)
    return api_client


def test_default_page(logged):
    body = logged.get("/api/t/numbers/").json()
    assert body["count"] == 120
    assert len(body["results"]) == 20
    assert body["next"]


def test_page_size_is_capped(logged):
    assert len(logged.get("/api/t/numbers/?page_size=100").json()["results"]) == 50
    assert len(logged.get("/api/t/numbers/?page_size=5").json()["results"]) == 5


def test_write_throttle(logged, monkeypatch):
    monkeypatch.setattr(WriteThrottle, "THROTTLE_RATES", {"api_write": "2/hour"})
    assert logged.post("/api/t/echo/").status_code == 201
    assert logged.post("/api/t/echo/").status_code == 201
    for _ in range(5):
        assert logged.get("/api/t/echo/").status_code == 200
    response = logged.post("/api/t/echo/")
    assert response.status_code == 429
    assert response.json()["code"] == "rate_limited"
    assert response.has_header("Retry-After")


def test_throttles_fail_open_when_cache_is_down(logged, monkeypatch):
    from rest_framework.throttling import SimpleRateThrottle

    def broken(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(SimpleRateThrottle.cache, "get", broken)
    assert logged.get("/api/t/echo/").status_code == 200
    assert logged.post("/api/t/echo/").status_code == 201


def test_write_rate_covers_business_limits():
    count, _period = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["api_write"].split("/")
    assert int(count) >= sum(settings.POSTS_RATE_LIMITS.values())


def test_max_page_size_constant():
    from api.pagination import PagePagination

    assert PagePagination.max_page_size == 50
    assert PagePagination.page_size == 20
