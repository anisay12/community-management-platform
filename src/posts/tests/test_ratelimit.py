import pytest
from django.core.cache import cache

from core.errors import DomainError
from posts import ratelimit

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_eleventh_post_in_the_hour_is_refused(active_user, settings):
    settings.POSTS_RATE_LIMITS = {"post": 10, "comment": 60, "reaction": 120}
    for _ in range(10):
        ratelimit.hit(active_user, "post")
    with pytest.raises(DomainError) as error:
        ratelimit.hit(active_user, "post")
    assert error.value.code == "rate_limited"
    assert 0 < error.value.retry_after <= 3600
    ratelimit.hit(active_user, "comment")  # buckets are independent


def test_limits_are_per_user(active_user, make_user, settings):
    settings.POSTS_RATE_LIMITS = {"post": 1}
    ratelimit.hit(active_user, "post")
    ratelimit.hit(make_user("bob@example.com"), "post")


def test_cache_down_fails_open(active_user, settings, monkeypatch):
    settings.POSTS_RATE_LIMITS = {"post": 0}

    def down(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(ratelimit.cache, "incr", down)
    ratelimit.hit(active_user, "post")
