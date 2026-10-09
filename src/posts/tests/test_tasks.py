from unittest import mock

import pytest
from django.core.cache import cache

from communities.models import CommunityMembership
from notifications.models import Notification
from posts import tasks
from posts.models import Comment, Post, Reaction

pytestmark = pytest.mark.django_db

Level = CommunityMembership.NotificationLevel


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def community(make_community):
    return make_community()


@pytest.fixture
def author(make_user, community, add_member):
    user = make_user("author@example.com", first_name="Ada", last_name="Author")
    add_member(community, user)
    return user


@pytest.fixture
def post(make_post, community, author):
    return make_post(community, author)


def _users(make_user, count, prefix="user"):
    return [
        make_user(f"{prefix}{index}@example.com", first_name="U", last_name=str(index))
        for index in range(count)
    ]


# Counters --------------------------------------------------------------------------


def test_recount_post(post, author, make_user, make_comment):
    readers = _users(make_user, 3)
    make_comment(post, author)
    make_comment(post, author, status=Comment.Status.HIDDEN, hidden_reason="r")
    reply_parent = make_comment(post, author)
    make_comment(post, author, parent=reply_parent)
    for reader in readers:
        Reaction.objects.create(user=reader, post=post, kind="useful")
    Reaction.objects.create(user=readers[0], post=post, kind="thanks")
    Post.objects.filter(pk=post.pk).update(comment_count=99, reaction_counts={"spam": 4})

    tasks.recount_target("posts.post", post.pk)

    post.refresh_from_db()
    assert post.comment_count == 3
    assert post.reaction_counts == {"useful": 3, "thanks": 1}


def test_recount_comment(post, author, make_user, make_comment):
    comment = make_comment(post, author)
    reader = _users(make_user, 1)[0]
    Reaction.objects.create(user=reader, comment=comment, kind="insightful")
    tasks.recount_target("posts.comment", comment.pk)
    comment.refresh_from_db()
    assert comment.reaction_counts == {"insightful": 1}


def test_recount_of_a_missing_or_unknown_target_is_a_no_op(post):
    tasks.recount_target("posts.post", 0)
    tasks.recount_target("posts.bookmark", post.pk)


def test_recount_releases_the_lock(post):
    cache.set(f"recount:posts.post:{post.pk}", 1, 10)
    tasks.recount_target("posts.post", post.pk)
    assert cache.get(f"recount:posts.post:{post.pk}") is None


def test_schedule_twice_within_the_lock_window_enqueues_once(
    post, django_capture_on_commit_callbacks
):
    with mock.patch.object(tasks.recount_target, "apply_async") as apply_async:
        with django_capture_on_commit_callbacks(execute=True):
            tasks.schedule_recount(post)
            tasks.schedule_recount(post)
        with django_capture_on_commit_callbacks(execute=True):
            tasks.schedule_recount(post)
    apply_async.assert_called_once_with(("posts.post", post.pk), countdown=3)


def test_schedule_runs_only_on_commit(post, django_capture_on_commit_callbacks):
    with mock.patch.object(tasks.recount_target, "apply_async") as apply_async:
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            tasks.schedule_recount(post)
        apply_async.assert_not_called()
        assert len(callbacks) == 1


def test_schedule_fails_open_without_cache(post, django_capture_on_commit_callbacks):
    with (
        mock.patch.object(tasks.recount_target, "apply_async") as apply_async,
        mock.patch.object(tasks.cache, "add", side_effect=ConnectionError),
        django_capture_on_commit_callbacks(execute=True),
    ):
        tasks.schedule_recount(post)
    apply_async.assert_called_once()


def test_schedule_swallows_broker_errors(post, django_capture_on_commit_callbacks):
    with (
        mock.patch.object(tasks.recount_target, "apply_async", side_effect=OSError),
        django_capture_on_commit_callbacks(execute=True),
    ):
        tasks.schedule_recount(post)
    assert cache.get(f"recount:posts.post:{post.pk}") is None


def test_verify_counters_fixes_drift(post, author, make_post, make_user, make_comment):
    reader = _users(make_user, 1)[0]
    comment = make_comment(post, author)
    Reaction.objects.create(user=reader, post=post, kind="useful")
    Reaction.objects.create(user=reader, comment=comment, kind="thanks")
    healthy = make_post(post.community, author, comment_count=0, reaction_counts={})
    Post.objects.filter(pk=post.pk).update(comment_count=7, reaction_counts={"useful": 5})
    Comment.objects.filter(pk=comment.pk).update(reaction_counts={"thanks": 0, "useful": 2})

    assert tasks.verify_counters() is None

    post.refresh_from_db()
    comment.refresh_from_db()
    healthy.refresh_from_db()
    assert (post.comment_count, post.reaction_counts) == (1, {"useful": 1})
    assert comment.reaction_counts == {"thanks": 1}
    assert (healthy.comment_count, healthy.reaction_counts) == (0, {})
    assert tasks.fix_counters() == 0
    Post.objects.filter(pk=post.pk).update(comment_count=3)
    Comment.objects.filter(pk=comment.pk).update(reaction_counts={})
    assert tasks.fix_counters() == 2


# Broadcast -------------------------------------------------------------------------


@pytest.fixture
def audience(make_user, community, add_member, author):
    users = {}
    for level in Level.values:
        user = make_user(f"{level}@example.com", first_name=level, last_name="Member")
        membership = add_member(community, user)
        membership.notification_level = level
        membership.save()
        users[level] = user
    # The author follows everything but is never notified of their own post.
    CommunityMembership.objects.filter(user=author).update(notification_level=Level.ALL)
    make_user("nonmember@example.com")
    return users


def _recipients(category):
    return set(
        Notification.objects.filter(category=category).values_list("recipient__email", flat=True)
    )


def test_broadcast_community_post_reaches_all_level_only(
    post, audience, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        tasks.broadcast_post(post.pk, "community_post")
    assert _recipients("community_post") == {"all@example.com"}
    notification = Notification.objects.get()
    assert notification.actor == post.author
    assert notification.community == post.community
    assert notification.target_id == str(post.public_id)


def test_broadcast_announcement_reaches_all_and_highlights(
    post, audience, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        tasks.broadcast_post(post.pk, "announcement")
    assert _recipients("announcement") == {"all@example.com", "highlights@example.com"}


def test_broadcast_skips_inactive_members(post, audience, django_capture_on_commit_callbacks):
    audience["all"].status = "suspended"
    audience["all"].save()
    with django_capture_on_commit_callbacks(execute=True):
        tasks.broadcast_post(post.pk, "community_post")
    assert not Notification.objects.exists()


def test_broadcast_in_batches(
    post, community, make_user, add_member, monkeypatch, django_capture_on_commit_callbacks
):
    for user in _users(make_user, 5, prefix="batch"):
        membership = add_member(community, user)
        membership.notification_level = Level.ALL
        membership.save()
    monkeypatch.setattr(tasks, "BROADCAST_BATCH_SIZE", 2)
    with (
        mock.patch.object(tasks, "notify", wraps=tasks.notify) as notify,
        django_capture_on_commit_callbacks(execute=True),
    ):
        tasks.broadcast_post(post.pk, "community_post")
    assert notify.call_count == 3
    assert [len(list(call.args[1])) for call in notify.call_args_list] == [2, 2, 1]
    assert Notification.objects.count() == 5


def test_broadcast_ignores_unpublished_missing_posts_and_unknown_categories(
    post, audience, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        tasks.broadcast_post(0, "community_post")
        tasks.broadcast_post(post.pk, "mention")
        Post.objects.filter(pk=post.pk).update(status=Post.Status.DRAFT)
        tasks.broadcast_post(post.pk, "announcement")
    assert not Notification.objects.exists()
