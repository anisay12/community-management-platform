"""Feed and thread selectors: ordering, pinned posts, filters, cursor stability, visibility."""

import base64
from datetime import timedelta

import pytest
from django.utils import timezone

from communities.models import Community
from communities.roles import CommunityRole
from posts.models import Comment, Post
from posts.selectors import (
    FEED_PAGE_SIZE,
    comment_thread,
    community_feed,
    decode_cursor,
    encode_cursor,
    home_feed,
    mark_share_access,
)

pytestmark = pytest.mark.django_db

S = Post.Status
K = Post.Kind


@pytest.fixture
def community(make_community):
    return make_community("Python guild", access_mode=Community.AccessMode.REQUEST)


@pytest.fixture
def author(make_user, community, add_member):
    user = make_user("author@example.com", first_name="Ann", last_name="Author")
    add_member(community, user)
    return user


@pytest.fixture
def reader(make_user, community, add_member):
    user = make_user("reader@example.com", first_name="Rob", last_name="Reader")
    add_member(community, user)
    return user


@pytest.fixture
def moderator(make_user, community, add_member):
    user = make_user("moderator@example.com", first_name="Mo", last_name="Derator")
    add_member(community, user, CommunityRole.MODERATOR)
    return user


def _at(minutes):
    return timezone.now() - timedelta(minutes=minutes)


def _titles(posts):
    return [post.title for post in posts]


# --- Community feed ---------------------------------------------------------------------------


def test_feed_orders_by_activity_and_excludes_drafts_and_archived(
    community, author, reader, make_post
):
    make_post(community, author, title="Old", last_activity_at=_at(30))
    make_post(community, author, title="New", last_activity_at=_at(1))
    make_post(community, author, title="Draft", status=S.DRAFT)
    make_post(community, author, title="Archived", status=S.ARCHIVED)
    page = community_feed(reader, community)
    assert _titles(page.items) == ["New", "Old"]
    assert page.next_cursor is None
    # The author does not see their own draft in the feed either.
    assert "Draft" not in _titles(community_feed(author, community).items)


def test_pending_and_hidden_posts_only_for_author_and_moderators(
    community, author, reader, moderator, make_post
):
    make_post(community, author, title="Pending", status=S.PENDING_REVIEW)
    make_post(community, author, title="Hidden", status=S.HIDDEN, hidden_reason="Off topic")
    assert _titles(community_feed(reader, community).items) == []
    assert sorted(_titles(community_feed(author, community).items)) == ["Hidden", "Pending"]
    assert sorted(_titles(community_feed(moderator, community).items)) == ["Hidden", "Pending"]


def test_non_member_of_private_community_gets_an_empty_feed(
    community, author, make_user, make_post
):
    make_post(community, author, title="Private")
    page = community_feed(make_user("outsider@example.com"), community)
    assert page.items == [] and page.pinned == []


def test_pinned_posts_first_page_only_and_not_repeated(community, author, reader, make_post):
    for index in range(FEED_PAGE_SIZE + 5):
        make_post(community, author, title=f"Post {index:02d}", last_activity_at=_at(100 - index))
    pinned_old = make_post(
        community, author, title="Pinned old", last_activity_at=_at(500), pinned_at=_at(10)
    )
    pinned_new = make_post(
        community, author, title="Pinned new", last_activity_at=_at(400), pinned_at=_at(5)
    )
    first = community_feed(reader, community)
    assert first.pinned == [pinned_new, pinned_old]
    assert len(first.items) == FEED_PAGE_SIZE
    assert not {pinned_new, pinned_old} & set(first.items)
    second = community_feed(reader, community, cursor=first.next_cursor)
    assert second.pinned == []
    assert len(second.items) == 5
    assert not {pinned_new, pinned_old} & set(second.items)
    assert second.next_cursor is None


def test_pinned_posts_are_capped_at_three(community, author, reader, make_post):
    for index in range(4):
        make_post(community, author, title=f"Pinned {index}", pinned_at=_at(index))
    assert len(community_feed(reader, community).pinned) == 3


def test_kind_filter_keeps_pinned_posts_in_place(community, author, reader, make_post):
    make_post(community, author, title="Question", kind=K.QUESTION, pinned_at=_at(1))
    make_post(community, author, title="Talk", kind=K.DISCUSSION)
    page = community_feed(reader, community, kind=K.QUESTION)
    assert page.pinned == []
    assert _titles(page.items) == ["Question"]
    # An unknown kind is ignored.
    assert len(community_feed(reader, community, kind="bogus").items) == 1


def test_unanswered_questions(community, author, reader, make_post, make_comment):
    make_post(community, author, title="Open question", kind=K.QUESTION)
    commented = make_post(community, author, title="Commented", kind=K.QUESTION)
    make_comment(commented, reader)
    hidden_only = make_post(community, author, title="Hidden reply only", kind=K.QUESTION)
    make_comment(hidden_only, reader, status=Comment.Status.HIDDEN)
    answered = make_post(community, author, title="Answered", kind=K.QUESTION)
    answer = make_comment(answered, reader, status=Comment.Status.HIDDEN)
    Post.objects.filter(pk=answered.pk).update(accepted_answer=answer)
    make_post(community, author, title="Discussion", kind=K.DISCUSSION)
    page = community_feed(reader, community, unanswered=True)
    assert sorted(_titles(page.items)) == ["Hidden reply only", "Open question"]
    assert page.pinned == []


def test_cursor_is_stable_when_posts_share_their_activity_time(
    community, author, reader, make_post
):
    moment = _at(5)
    created = {
        make_post(community, author, title=f"Same {i}", last_activity_at=moment).pk
        for i in range(FEED_PAGE_SIZE * 2 + 3)
    }
    seen, cursor, pages = [], None, 0
    while True:
        page = community_feed(reader, community, cursor=cursor)
        seen.extend(post.pk for post in page.items)
        pages += 1
        if page.next_cursor is None:
            break
        # A newer post arriving between two pages neither shifts nor repeats the next page.
        make_post(community, author, title=f"Late {pages}", last_activity_at=timezone.now())
        cursor = page.next_cursor
    assert pages == 3
    assert len(seen) == len(set(seen))
    assert set(seen) == created


def test_cursor_round_trip_and_invalid_values(community, author, make_post):
    post = make_post(community, author)
    assert decode_cursor(encode_cursor(post)) == (post.last_activity_at, post.pk)
    for value in ("", "not-base64!", "Zm9v", encode_cursor(post)[:-4] + "AAAA"):
        assert decode_cursor(value) is None


@pytest.mark.parametrize("moment", ["0001-01-01T00:00:00+20:00", "9999-12-31T23:59:59-20:00"])
def test_cursor_with_an_out_of_range_offset_is_invalid(moment):
    value = base64.urlsafe_b64encode(f"{moment}|5".encode()).decode()
    assert decode_cursor(value) is None


def test_cursor_is_normalised_to_utc():
    value = base64.urlsafe_b64encode(b"2026-01-01T12:00:00+02:00|5").decode()
    moment, pk = decode_cursor(value)
    assert (moment.isoformat(), pk) == ("2026-01-01T10:00:00+00:00", 5)


def test_invalid_cursor_falls_back_to_the_first_page(community, author, reader, make_post):
    make_post(community, author, title="Only", pinned_at=_at(1))
    page = community_feed(reader, community, cursor="garbage")
    assert _titles(page.pinned) == ["Only"]


# --- Home feed --------------------------------------------------------------------------------


def test_home_feed_lists_published_posts_of_member_communities(
    community, author, reader, make_community, make_post, add_member
):
    other = make_community("Data guild")
    elsewhere = make_community("Open but not joined")
    add_member(other, reader)
    make_post(community, author, title="Mine old", last_activity_at=_at(20))
    make_post(other, author, title="Mine new", last_activity_at=_at(1))
    make_post(elsewhere, author, title="Not followed")
    make_post(community, author, title="Pending", status=S.PENDING_REVIEW)
    make_post(community, reader, title="My draft", status=S.DRAFT)
    make_post(community, author, title="Archived", status=S.ARCHIVED)
    page = home_feed(reader)
    assert _titles(page.items) == ["Mine new", "Mine old"]
    assert page.pinned == []


def test_home_feed_paginates_with_cursor(community, author, reader, make_post):
    for index in range(FEED_PAGE_SIZE + 1):
        make_post(community, author, title=f"P{index}", last_activity_at=_at(index))
    first = home_feed(reader)
    second = home_feed(reader, cursor=first.next_cursor)
    assert len(first.items) == FEED_PAGE_SIZE
    assert _titles(second.items) == [f"P{FEED_PAGE_SIZE}"]


def test_home_feed_empty_for_anonymous_or_suspended(community, author, make_post):
    from django.contrib.auth.models import AnonymousUser

    make_post(community, author)
    assert home_feed(AnonymousUser()).items == []


# --- Shares -----------------------------------------------------------------------------------


def test_mark_share_access_uses_one_query(
    community, author, reader, make_community, make_post, add_member, django_assert_num_queries
):
    secret = make_community("Secret", access_mode=Community.AccessMode.INVITE)
    add_member(secret, author)
    open_one = make_community("Open")
    hidden_original = make_post(secret, author, title="Secret original")
    visible_original = make_post(open_one, author, title="Open original")
    shares = [
        make_post(community, author, title="Share 1", shared_from=hidden_original),
        make_post(community, author, title="Share 2", shared_from=visible_original),
        make_post(community, author, title="Plain"),
    ]
    # One query for the originals, plus the viewer's roles (memoised afterwards).
    with django_assert_num_queries(2):
        mark_share_access(reader, shares)
    assert [post.share_original_visible for post in shares] == [False, True, False]
    with django_assert_num_queries(0):
        mark_share_access(reader, [shares[2]])


# --- Comment thread ---------------------------------------------------------------------------


def test_comment_thread_two_levels_and_hidden_masking(
    community, author, reader, moderator, make_user, make_post, make_comment, add_member
):
    post = make_post(community, author)
    first = make_comment(post, reader, body="First")
    reply = make_comment(post, author, parent=first, body="Reply")
    hidden = make_comment(post, reader, body="Rude", status=Comment.Status.HIDDEN)
    hidden_reply = make_comment(
        post, reader, parent=first, body="Rude reply", status=Comment.Status.HIDDEN
    )
    other = make_user("other@example.com")
    add_member(community, other)

    thread = comment_thread(other, post)
    assert thread == [first, hidden]
    assert thread[0].prefetched_replies == [reply, hidden_reply]
    assert [c.body_visible for c in (thread[0], thread[1])] == [True, False]
    assert [c.body_visible for c in thread[0].prefetched_replies] == [True, False]

    for viewer in (reader, moderator):
        thread = comment_thread(viewer, post)
        assert all(c.body_visible for c in thread)
        assert all(c.body_visible for c in thread[0].prefetched_replies)


def test_comment_thread_takes_the_moderator_flag_from_the_caller(
    community, author, reader, make_post, make_comment
):
    post = make_post(community, author)
    make_comment(post, author, body="Rude", status=Comment.Status.HIDDEN)
    assert [c.body_visible for c in comment_thread(reader, post)] == [False]
    assert [c.body_visible for c in comment_thread(reader, post, moderator=True)] == [True]


def test_comment_thread_query_count(
    community, author, reader, make_post, make_comment, django_assert_max_num_queries
):
    post = make_post(community, author)
    for _ in range(10):
        top = make_comment(post, reader)
        make_comment(post, author, parent=top)
        make_comment(post, author, parent=top)
    reader = type(reader).objects.get(pk=reader.pk)
    with django_assert_max_num_queries(4):
        thread = comment_thread(reader, post)
    assert len(thread) == 10 and all(len(c.prefetched_replies) == 2 for c in thread)
