import threading

import pytest
from django.core.cache import cache
from django.db import connection

from audit.models import AuditEvent
from communities.models import CommunityMembership
from core.errors import DomainError
from posts import services_interactions as services
from posts.models import ContentReport, Post, Reaction

# serialized_rollback restores the migration-seeded rows (groups, categories) after the flush.
pytestmark = pytest.mark.django_db(transaction=True, serialized_rollback=True)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _run_concurrently(targets):
    barrier = threading.Barrier(len(targets))
    results, errors, crashes = [], [], []

    def worker(target):
        try:
            barrier.wait()
            results.append(target())
        except DomainError as error:
            errors.append(error.code)
        except Exception as error:  # a 500 in production
            crashes.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(target,)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results, errors, crashes


@pytest.fixture
def setup(make_community, make_user, add_member, make_post):
    community = make_community()
    author = make_user("author@example.com", first_name="Ada", last_name="Author")
    add_member(community, author)
    post = make_post(community, author)
    return community, post


def test_simultaneous_identical_reactions_create_one_row(setup, make_user, add_member):
    community, post = setup
    reader = make_user("reader@example.com", first_name="Remi", last_name="Reader")
    add_member(community, reader)

    def react():
        return services.toggle_reaction(
            actor=reader, target=Post.objects.get(pk=post.pk), kind="useful"
        )

    results, errors, crashes = _run_concurrently([react, react])
    assert crashes == []
    assert errors == []
    assert Reaction.objects.filter(user=reader, post=post).count() == 1
    assert True in results


def test_simultaneous_reports_reaching_the_threshold_hide_once(setup, make_user, settings):
    settings.POSTS_REPORT_AUTOHIDE_THRESHOLD = 2
    _community, post = setup
    reporters = [
        make_user(f"r{index}@example.com", first_name="R", last_name=str(index))
        for index in range(2)
    ]

    def report_by(user):
        return lambda: services.report(
            actor=user, target=Post.objects.get(pk=post.pk), reason="spam"
        )

    _results, errors, crashes = _run_concurrently([report_by(user) for user in reporters])
    assert crashes == []
    assert errors == []
    assert ContentReport.objects.filter(post=post, status="open").count() == 2
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN
    assert AuditEvent.objects.filter(action="post.auto_hidden").count() == 1


def test_simultaneous_dismissals_of_the_last_reports_restore_the_target(setup, make_user, settings):
    settings.POSTS_REPORT_AUTOHIDE_THRESHOLD = 2
    community, post = setup
    moderator = make_user("mod@example.com", first_name="Mo", last_name="Derator")
    CommunityMembership.objects.create(
        community=community, user=moderator, role=CommunityMembership.Role.MODERATOR
    )
    reports = [
        services.report(
            actor=make_user(f"r{index}@example.com", first_name="R", last_name=str(index)),
            target=Post.objects.get(pk=post.pk),
            reason="spam",
        )
        for index in range(2)
    ]
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN

    def dismiss(report):
        return lambda: services.dismiss_report(
            actor=moderator, report=ContentReport.objects.get(pk=report.pk)
        )

    _results, errors, crashes = _run_concurrently([dismiss(report) for report in reports])
    assert crashes == []
    assert errors == []
    post.refresh_from_db()
    assert post.status == Post.Status.PUBLISHED
    assert AuditEvent.objects.filter(action="post.unhidden").count() == 1
