import threading

import pytest
from django.db import connection
from django.utils.text import slugify

from communities import services
from communities.models import (
    Community,
    CommunityCategory,
    CommunityMembership,
    MembershipRequest,
)
from core.errors import DomainError

# serialized_rollback restores the migration-seeded rows (groups, categories) after the flush.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


def _community(access_mode):
    category = CommunityCategory.objects.create(name="Concurrency", slug="concurrency")
    return Community.objects.create(
        name="Race", slug=slugify("Race"), category=category, tagline="t", access_mode=access_mode
    )


def _run_concurrently(target, count=2):
    barrier = threading.Barrier(count)
    results, errors = [], []

    def worker():
        try:
            barrier.wait()
            results.append(target())
        except DomainError as error:
            errors.append(error.code)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results, errors


def test_concurrent_requests_create_one_pending_request(make_user):
    community = _community(Community.AccessMode.REQUEST)
    user = make_user()
    results, errors = _run_concurrently(lambda: services.join(actor=user, community=community))
    assert errors == []
    assert len({r.pk for r in results}) == 1
    assert MembershipRequest.objects.filter(status="pending").count() == 1


def test_concurrent_joins_create_one_membership(make_user):
    community = _community(Community.AccessMode.OPEN)
    user = make_user()
    results, errors = _run_concurrently(lambda: services.join(actor=user, community=community))
    assert len(results) == 1
    assert errors == ["already_member"]
    assert CommunityMembership.objects.filter(community=community).count() == 1
    community.refresh_from_db()
    assert community.member_count == 1


def test_concurrent_joins_of_many_users_keep_member_count_exact(make_user):
    community = _community(Community.AccessMode.OPEN)
    users = [make_user(f"user{i}@example.com") for i in range(5)]
    iterator = iter(users)
    lock = threading.Lock()

    def join_next():
        with lock:
            user = next(iterator)
        return services.join(actor=user, community=community)

    _results, errors = _run_concurrently(join_next, count=5)
    assert errors == []
    community.refresh_from_db()
    assert community.member_count == 5 == CommunityMembership.objects.count()
