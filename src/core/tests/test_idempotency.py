import uuid

import pytest
from django.core.cache import cache

from core import idempotency
from core.errors import DomainError


class _User:
    pk = 7


@pytest.fixture(autouse=True)
def _empty_cache():
    cache.clear()


def test_runs_once_and_replays_the_result():
    calls = []
    key = idempotency.new_key()

    def operation():
        calls.append(1)
        return "result"

    assert idempotency.run_once(_User(), "form", key, operation) == ("result", False)
    assert idempotency.run_once(_User(), "form", key, operation) == ("result", True)
    assert len(calls) == 1
    # Another key, form or user runs again.
    assert idempotency.run_once(_User(), "other", key, operation) == ("result", False)
    assert idempotency.run_once(_User(), "form", uuid.uuid4(), operation)[1] is False


def test_failure_releases_the_key():
    key = idempotency.new_key()

    def boom():
        raise DomainError("x", "No")

    with pytest.raises(DomainError):
        idempotency.run_once(_User(), "form", key, boom)
    assert idempotency.run_once(_User(), "form", key, lambda: 1) == (1, False)


def test_in_flight_submission_is_refused():
    key = idempotency.new_key()
    cache.add(idempotency.cache_key(_User(), "form", key), "__in_flight__")
    with pytest.raises(DomainError) as error:
        idempotency.run_once(_User(), "form", key, lambda: 1)
    assert error.value.code == "submission_in_progress"
