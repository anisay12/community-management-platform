import threading

import pytest
from django.core.cache import cache
from django.db import connection

from audit.models import AuditEvent
from communities.models import CommunityMembership
from core.errors import DomainError
from posts import services_interactions as services
from posts import services_posts
from posts.models import Bookmark, ContentReport, Post, Reaction
from taxonomy.models import Tag
from taxonomy.services import get_or_create_tag, merge_tags

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
        return services.set_reaction(
            actor=reader, target=Post.objects.get(pk=post.pk), kind="useful", present=True
        )

    # Both requests ask for the reaction to be present (double click, two tabs): one row,
    # whatever the order in which they run.
    results, errors, crashes = _run_concurrently([react, react])
    assert crashes == []
    assert errors == []
    assert results == [True, True]
    assert Reaction.objects.filter(user=reader, post=post).count() == 1


def test_simultaneous_identical_bookmarks_create_one_row(setup, make_user, add_member):
    community, post = setup
    reader = make_user("reader@example.com", first_name="Remi", last_name="Reader")
    add_member(community, reader)

    def bookmark():
        return services.set_bookmark(actor=reader, post=Post.objects.get(pk=post.pk), present=True)

    results, errors, crashes = _run_concurrently([bookmark, bookmark])
    assert (crashes, errors, results) == ([], [], [True, True])
    assert Bookmark.objects.filter(user=reader, post=post).count() == 1


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


def test_merging_a_tag_while_an_editor_saves_it_never_crashes(setup, functional_admin):
    """The editor references the tag being merged away: it either saves before the merge (the
    merge then moves its link) or is refused, never a foreign key error or a deadlock."""
    _community, post = setup
    author = post.author
    for round_ in range(5):
        target = get_or_create_tag(f"Machine learning {round_}")
        source = get_or_create_tag(f"ML {round_}")
        version = Post.objects.get(pk=post.pk).version

        def merge(source=source, target=target):
            return merge_tags(actor=functional_admin, source=source, target=target)

        def edit(source=source, version=version):
            return services_posts.update_post(
                actor=author,
                post=Post.objects.get(pk=post.pk),
                title="Edited",
                body="Body",
                tags=[source.name],
                version=version,
            )

        _results, errors, crashes = _run_concurrently([merge, edit])
        assert crashes == []
        assert set(errors) <= {"edit_conflict", "tag_unavailable", "tag_creation_forbidden"}
        assert not Tag.objects.filter(pk=source.pk).exists()
