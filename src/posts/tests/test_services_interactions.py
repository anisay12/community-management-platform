from unittest import mock

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from audit.models import AuditEvent
from communities.models import Community, CommunityMembership
from core.errors import DomainError
from notifications.models import Notification
from posts import services_interactions as services
from posts.models import (
    COMMENT_BODY_MAX_LENGTH,
    Bookmark,
    BookmarkCollection,
    Comment,
    Mention,
    Post,
    Reaction,
)

pytestmark = pytest.mark.django_db

Role = CommunityMembership.Role


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
def reader(make_user, community, add_member):
    user = make_user("reader@example.com", first_name="Remi", last_name="Reader")
    add_member(community, user)
    return user


@pytest.fixture
def moderator(make_user, community, add_member):
    user = make_user("mod@example.com", first_name="Mona", last_name="Moderator")
    add_member(community, user, Role.MODERATOR)
    return user


@pytest.fixture
def outsider(make_user):
    return make_user("outsider@example.com", first_name="Otto", last_name="Outsider")


@pytest.fixture
def post(make_post, community, author):
    return make_post(community, author, kind=Post.Kind.QUESTION)


def _code(callable_, **kwargs):
    with pytest.raises(DomainError) as error:
        callable_(**kwargs)
    return error.value.code


def _notifications(category, user=None):
    rows = Notification.objects.filter(category=category)
    if user is not None:
        rows = rows.filter(recipient=user)
    return rows


# Comments --------------------------------------------------------------------------


def test_add_comment_stores_rendered_body_and_activity(
    post, reader, django_capture_on_commit_callbacks
):
    before = post.last_activity_at
    with django_capture_on_commit_callbacks(execute=True):
        comment = services.add_comment(actor=reader, post=post, body="Hello **there**")
    assert comment.author == reader
    assert comment.author_display == "Remi Reader"
    assert "<strong>there</strong>" in comment.body_html
    assert comment.parent is None
    assert comment.is_expert_answer is False
    post.refresh_from_db()
    assert post.last_activity_at > before
    assert post.comment_count == 1
    post.community.refresh_from_db()
    assert post.community.last_activity_at >= post.last_activity_at


def test_add_comment_notifies_the_post_author_not_the_actor(
    post, author, reader, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        services.add_comment(actor=reader, post=post, body="First")
        services.add_comment(actor=author, post=post, body="Own reply")
    assert list(_notifications("reply").values_list("recipient", flat=True)) == [author.pk]


def test_reply_to_a_reply_attaches_to_the_top_level_comment(
    post, author, reader, moderator, make_comment, django_capture_on_commit_callbacks
):
    top = make_comment(post, author)
    reply = make_comment(post, moderator, parent=top)
    with django_capture_on_commit_callbacks(execute=True):
        nested = services.add_comment(actor=reader, post=post, body="Nested", parent=reply)
    assert nested.parent == top
    # The reply notification goes to the author of the comment being answered.
    recipients = set(_notifications("reply").values_list("recipient", flat=True))
    assert recipients == {moderator.pk}


def test_parent_of_another_post_is_refused(post, reader, author, make_post, make_comment):
    other = make_comment(make_post(post.community, author), author)
    code = _code(services.add_comment, actor=reader, post=post, body="x", parent=other)
    assert code == "depth_exceeded"


def test_reply_to_a_hidden_comment_is_refused(post, reader, author, make_comment):
    hidden = make_comment(post, author, status=Comment.Status.HIDDEN, hidden_reason="r")
    code = _code(services.add_comment, actor=reader, post=post, body="x", parent=hidden)
    assert code in {"invalid_state", "forbidden"}


def test_reply_to_a_hidden_reply_is_refused(
    post, reader, author, moderator, make_comment, django_capture_on_commit_callbacks
):
    top = make_comment(post, author)
    hidden = make_comment(post, moderator, parent=top, status=Comment.Status.HIDDEN,
                          hidden_reason="r")  # fmt: skip
    with django_capture_on_commit_callbacks(execute=True):
        code = _code(services.add_comment, actor=reader, post=post, body="x", parent=hidden)
    assert code == "invalid_state"
    assert not _notifications("reply").exists()


def test_reply_notification_only_for_readers(
    make_community, make_user, make_post, make_comment, add_member,
    django_capture_on_commit_callbacks,
):  # fmt: skip
    private = make_community("Private", access_mode=Community.AccessMode.INVITE)
    left = make_user("left@example.com", first_name="Lea", last_name="Left")
    stayer = make_user("stay@example.com", first_name="Sam", last_name="Stayer")
    for user in (left, stayer):
        add_member(private, user)
    post = make_post(private, left)
    top = make_comment(post, left)
    CommunityMembership.objects.filter(community=private, user=left).delete()
    with django_capture_on_commit_callbacks(execute=True):
        services.add_comment(actor=stayer, post=post, body="On the post")
        services.add_comment(actor=stayer, post=post, body="On the comment", parent=top)
    assert not _notifications("reply").exists()


@pytest.mark.parametrize("role", [Role.EXPERT, Role.OWNER])
def test_expert_answer_flag_for_expert_and_above(post, make_user, add_member, role):
    expert = make_user("expert@example.com", first_name="Eve", last_name="Expert")
    add_member(post.community, expert, role)
    comment = services.add_comment(actor=expert, post=post, body="Answer")
    assert comment.is_expert_answer is True


def test_contributor_is_not_expert(post, make_user, add_member):
    user = make_user("contrib@example.com", first_name="Cy", last_name="Contrib")
    add_member(post.community, user, Role.CONTRIBUTOR)
    assert services.add_comment(actor=user, post=post, body="x").is_expert_answer is False


def test_comment_needs_membership(post, outsider):
    assert _code(services.add_comment, actor=outsider, post=post, body="x") == "not_member"


def test_comment_on_private_post_by_non_member_is_forbidden(
    make_community, make_post, author, add_member, outsider
):
    private = make_community("Private", access_mode=Community.AccessMode.INVITE)
    add_member(private, author)
    secret = make_post(private, author)
    assert _code(services.add_comment, actor=outsider, post=secret, body="x") == "forbidden"


def test_comment_in_read_only_community(post, reader):
    Community.objects.filter(pk=post.community_id).update(status=Community.Status.SUSPENDED)
    post.community.refresh_from_db()
    assert _code(services.add_comment, actor=reader, post=post, body="x") == "read_only"


@pytest.mark.parametrize("status", [Post.Status.ARCHIVED, Post.Status.DRAFT])
def test_comment_needs_a_published_post(make_post, community, author, reader, status):
    target = make_post(community, author, status=status)
    assert _code(services.add_comment, actor=reader, post=target, body="x") in {
        "invalid_state",
        "forbidden",
    }


def test_comment_on_archived_post_is_invalid_state(make_post, community, author, reader):
    archived = make_post(community, author, status=Post.Status.ARCHIVED)
    assert _code(services.add_comment, actor=reader, post=archived, body="x") == "invalid_state"


def test_comment_body_validation(post, reader):
    assert _code(services.add_comment, actor=reader, post=post, body="   ") == "body_required"
    too_long = "x" * 10_001
    assert _code(services.add_comment, actor=reader, post=post, body=too_long) == "body_too_long"


def test_comment_rate_limit(post, reader, settings):
    settings.POSTS_RATE_LIMITS = {**settings.POSTS_RATE_LIMITS, "comment": 2}
    services.add_comment(actor=reader, post=post, body="1")
    services.add_comment(actor=reader, post=post, body="2")
    assert _code(services.add_comment, actor=reader, post=post, body="3") == "rate_limited"


def test_refused_writes_do_not_count_towards_the_rate_limit(post, reader, author, settings):
    settings.POSTS_RATE_LIMITS = {**settings.POSTS_RATE_LIMITS, "comment": 1, "reaction": 1}
    assert _code(services.add_comment, actor=reader, post=post, body=" ") == "body_required"
    too_long = "x" * (COMMENT_BODY_MAX_LENGTH + 1)
    assert _code(services.add_comment, actor=reader, post=post, body=too_long) == "body_too_long"
    services.add_comment(actor=reader, post=post, body="Counted")
    assert _code(services.add_comment, actor=reader, post=post, body="Again") == "rate_limited"
    react = {"target": post, "present": True}
    assert _code(services.set_reaction, actor=author, kind="useful", **react) == "own_content"
    assert _code(services.set_reaction, actor=reader, kind="nope", **react) == "invalid_state"
    services.set_reaction(actor=reader, kind="useful", **react)
    assert _code(services.set_reaction, actor=reader, kind="thanks", **react) == "rate_limited"


def test_mentions_notified_once_per_newly_mentioned_user(
    post, reader, author, moderator, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        comment = services.add_comment(
            actor=reader, post=post, body="Ping @mona.moderator and @remi.reader"
        )
    mentioned = set(
        Mention.objects.filter(comment=comment).values_list("mentioned_user", flat=True)
    )
    assert mentioned == {moderator.pk}  # the actor's own handle is not a mention
    assert list(_notifications("mention").values_list("recipient", flat=True)) == [moderator.pk]
    assert '<span class="mention">@mona.moderator</span>' in comment.body_html

    with django_capture_on_commit_callbacks(execute=True):
        services.update_comment(
            actor=reader, comment=comment, body="Ping @ada.author and @nobody.here"
        )
    recipients = sorted(_notifications("mention").values_list("recipient", flat=True))
    assert recipients == sorted([moderator.pk, author.pk])
    comment.refresh_from_db()
    assert '<span class="mention">@ada.author</span>' in comment.body_html
    assert '<span class="mention">@nobody.here' not in comment.body_html


def test_update_comment_by_author(post, reader, make_comment):
    comment = make_comment(post, reader)
    updated = services.update_comment(actor=reader, comment=comment, body="New *text*")
    comment.refresh_from_db()
    assert comment.body == "New *text*" and "<em>text</em>" in comment.body_html
    assert updated.pk == comment.pk


def test_update_comment_by_moderator_and_refused_for_others(
    post, reader, author, moderator, make_comment
):
    comment = make_comment(post, reader)
    services.update_comment(actor=moderator, comment=comment, body="Moderated")
    comment.refresh_from_db()
    assert comment.body == "Moderated"
    assert _code(services.update_comment, actor=author, comment=comment, body="x") == "forbidden"


def test_moderator_edit_of_a_comment_is_audited_and_tells_its_author(
    post, reader, moderator, make_comment, django_capture_on_commit_callbacks
):
    comment = make_comment(post, reader)
    with django_capture_on_commit_callbacks(execute=True):
        services.update_comment(actor=moderator, comment=comment, body="Moderated")
    event = AuditEvent.objects.get(action="comment.moderator_edited")
    assert (event.actor, event.community_id) == (moderator, post.community_id)
    notification = _notifications("system", reader).get()
    assert notification.actor == moderator
    # Their own comment, or an author editing theirs: neither audited nor notified.
    with django_capture_on_commit_callbacks(execute=True):
        mine = make_comment(post, moderator)
        services.update_comment(actor=moderator, comment=mine, body="Mine")
        services.update_comment(actor=reader, comment=comment, body="Back")
    assert AuditEvent.objects.filter(action="comment.moderator_edited").count() == 1
    assert _notifications("system").count() == 1


def _former_member_comment(make_community, make_user, make_post, make_comment, add_member):
    """A comment of a member who then left the private community, and its moderator."""
    private = make_community("Private", access_mode=Community.AccessMode.INVITE)
    left = make_user("left@example.com", first_name="Lea", last_name="Left")
    moderator = make_user("pmod@example.com", first_name="Pia", last_name="Mod")
    add_member(private, left)
    add_member(private, moderator, Role.MODERATOR)
    comment = make_comment(make_post(private, moderator), left)
    CommunityMembership.objects.filter(community=private, user=left).delete()
    return comment, moderator


def test_moderator_edit_not_notified_to_a_former_member(
    make_community, make_user, make_post, make_comment, add_member,
    django_capture_on_commit_callbacks,
):  # fmt: skip
    comment, moderator = _former_member_comment(
        make_community, make_user, make_post, make_comment, add_member
    )
    with django_capture_on_commit_callbacks(execute=True):
        services.update_comment(actor=moderator, comment=comment, body="Moderated")
    assert AuditEvent.objects.filter(action="comment.moderator_edited").exists()
    assert not _notifications("system").exists()


def test_update_comment_on_an_archived_post_is_refused(post, reader, moderator, make_comment):
    comment = make_comment(post, reader)
    Post.objects.filter(pk=post.pk).update(status=Post.Status.ARCHIVED)
    comment.refresh_from_db()
    for actor in (reader, moderator):
        code = _code(services.update_comment, actor=actor, comment=comment, body="x")
        assert code == "invalid_state"
    comment.refresh_from_db()
    assert comment.body == "A comment"


def test_update_comment_in_read_only_community(post, reader, make_comment):
    comment = make_comment(post, reader)
    Community.objects.filter(pk=post.community_id).update(status=Community.Status.ARCHIVED)
    post.community.refresh_from_db()
    assert _code(services.update_comment, actor=reader, comment=comment, body="x") == "read_only"


def test_hide_and_unhide_comment_audited(
    post, reader, moderator, make_comment, django_capture_on_commit_callbacks
):
    comment = make_comment(post, reader)
    with django_capture_on_commit_callbacks(execute=True):
        services.hide_comment(actor=moderator, comment=comment, reason="Rude")
    comment.refresh_from_db()
    assert comment.status == Comment.Status.HIDDEN
    event = AuditEvent.objects.get(action="comment.hidden")
    assert event.changes == {"reason": "Rude"} and event.community_id == post.community_id
    assert _notifications("system", reader).exists()
    post.refresh_from_db()
    assert post.comment_count == 0

    with django_capture_on_commit_callbacks(execute=True):
        services.unhide_comment(actor=moderator, comment=comment)
    comment.refresh_from_db()
    assert comment.status == Comment.Status.VISIBLE
    assert AuditEvent.objects.filter(action="comment.unhidden", actor=moderator).exists()
    post.refresh_from_db()
    assert post.comment_count == 1


def test_hide_comment_not_notified_to_a_former_member(
    make_community, make_user, make_post, make_comment, add_member,
    django_capture_on_commit_callbacks,
):  # fmt: skip
    comment, moderator = _former_member_comment(
        make_community, make_user, make_post, make_comment, add_member
    )
    with django_capture_on_commit_callbacks(execute=True):
        services.hide_comment(actor=moderator, comment=comment, reason="Rude")
    assert AuditEvent.objects.filter(action="comment.hidden").exists()
    assert not _notifications("system").exists()


def test_hide_comment_needs_reason_and_moderator(post, reader, author, moderator, make_comment):
    comment = make_comment(post, reader)
    assert (
        _code(services.hide_comment, actor=moderator, comment=comment, reason=" ")
        == "reason_required"
    )
    assert _code(services.hide_comment, actor=author, comment=comment, reason="x") == "forbidden"
    assert _code(services.unhide_comment, actor=author, comment=comment) == "forbidden"


def test_hide_comment_in_read_only_community_is_allowed(post, reader, moderator, make_comment):
    comment = make_comment(post, reader)
    Community.objects.filter(pk=post.community_id).update(status=Community.Status.SUSPENDED)
    post.community.refresh_from_db()
    services.hide_comment(actor=moderator, comment=comment, reason="Spam")
    comment.refresh_from_db()
    assert comment.status == Comment.Status.HIDDEN


def _for_update_tables(context) -> list[str]:
    """The tables locked ``FOR UPDATE`` in ``context``, in order."""
    return [
        query["sql"].split(" FROM ")[1].split()[0].strip('"')
        for query in context.captured_queries
        if query["sql"].startswith("SELECT") and "FOR UPDATE" in query["sql"]
    ]


def test_add_comment_locks_the_post_and_reads_its_status_again(post, reader, make_comment):
    Post.objects.filter(pk=post.pk).update(status=Post.Status.ARCHIVED)  # ``post`` is stale
    assert _code(services.add_comment, actor=reader, post=post, body="Late") == "invalid_state"
    assert not Comment.objects.exists()
    Post.objects.filter(pk=post.pk).update(status=Post.Status.PUBLISHED)
    with CaptureQueriesContext(connection) as context:
        services.add_comment(actor=reader, post=post, body="On time")
    assert _for_update_tables(context)[0] == "posts_post"


def test_set_reaction_locks_the_post_and_reads_the_target_again(post, reader, author, make_comment):
    comment = make_comment(post, author)
    Comment.objects.filter(pk=comment.pk).update(status=Comment.Status.HIDDEN)
    react = {"actor": reader, "kind": "useful", "present": True}
    assert _code(services.set_reaction, target=comment, **react) == "forbidden"  # now hidden
    Post.objects.filter(pk=post.pk).update(status=Post.Status.ARCHIVED)
    assert _code(services.set_reaction, target=post, **react) == "invalid_state"
    assert not Reaction.objects.exists()
    Post.objects.filter(pk=post.pk).update(status=Post.Status.PUBLISHED)
    Comment.objects.filter(pk=comment.pk).update(status=Comment.Status.VISIBLE)
    with CaptureQueriesContext(connection) as context:
        services.set_reaction(actor=reader, target=comment, kind="useful", present=True)
    assert _for_update_tables(context)[:2] == ["posts_post", "posts_comment"]


# Reactions -------------------------------------------------------------------------


def test_set_reaction_on_and_off(post, reader, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        assert services.set_reaction(actor=reader, target=post, kind="useful", present=True)
        assert services.set_reaction(actor=reader, target=post, kind="thanks", present=True)
    post.refresh_from_db()
    assert post.reaction_counts == {"useful": 1, "thanks": 1}
    with django_capture_on_commit_callbacks(execute=True):
        assert services.set_reaction(actor=reader, target=post, kind="useful", present=False) is (
            False
        )
    post.refresh_from_db()
    assert post.reaction_counts == {"thanks": 1}
    assert Reaction.objects.filter(user=reader).count() == 1


def test_set_reaction_is_idempotent(post, reader):
    """A repeated request (double click, two tabs, retry) leaves the state it asked for."""
    for _attempt in range(2):
        assert services.set_reaction(actor=reader, target=post, kind="useful", present=True)
    assert Reaction.objects.filter(user=reader, post=post, kind="useful").count() == 1
    for _attempt in range(2):
        assert not services.set_reaction(actor=reader, target=post, kind="useful", present=False)
    assert not Reaction.objects.filter(user=reader).exists()


def test_reaction_on_comment(
    post, author, reader, make_comment, django_capture_on_commit_callbacks
):
    comment = make_comment(post, author)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.set_reaction(actor=reader, target=comment, kind="insightful", present=True)
    comment.refresh_from_db()
    assert comment.reaction_counts == {"insightful": 1}


def test_reaction_on_own_content_refused(post, author):
    assert _code(services.set_reaction, actor=author, target=post, kind="useful", present=True) == (
        "own_content"
    )


@pytest.mark.parametrize("status", [Post.Status.HIDDEN, Post.Status.ARCHIVED])
def test_reaction_on_hidden_or_archived_post_refused(make_post, community, author, reader, status):
    target = make_post(community, author, status=status)
    code = _code(services.set_reaction, actor=reader, target=target, kind="useful", present=True)
    assert code in {"invalid_state", "forbidden"}


def test_reaction_on_archived_post_is_invalid_state(make_post, community, author, reader):
    target = make_post(community, author, status=Post.Status.ARCHIVED)
    assert _code(
        services.set_reaction, actor=reader, target=target, kind="useful", present=True
    ) == ("invalid_state")


def test_reaction_on_hidden_comment_refused(post, author, reader, moderator, make_comment):
    comment = make_comment(post, author, status=Comment.Status.HIDDEN, hidden_reason="r")
    # A reader who cannot see the comment learns nothing about it (404 in the views).
    code = _code(services.set_reaction, actor=reader, target=comment, kind="useful", present=True)
    assert code == "forbidden"
    code = _code(
        services.set_reaction, actor=moderator, target=comment, kind="useful", present=True
    )
    assert code == "invalid_state"


def test_report_of_a_hidden_comment_does_not_leak_it(post, author, reader, moderator,
                                                     make_comment):  # fmt: skip
    comment = make_comment(post, author, status=Comment.Status.HIDDEN, hidden_reason="r")
    assert _code(services.report, actor=reader, target=comment, reason="spam") == "forbidden"
    assert _code(services.report, actor=moderator, target=comment, reason="spam") == (
        "invalid_state"
    )


def test_reaction_unknown_kind_and_non_member(post, reader, outsider):
    assert _code(services.set_reaction, actor=reader, target=post, kind="love", present=True) == (
        "invalid_state"
    )
    assert _code(
        services.set_reaction, actor=outsider, target=post, kind="useful", present=True
    ) == ("not_member")


def test_reaction_in_read_only_community(post, reader):
    Community.objects.filter(pk=post.community_id).update(status=Community.Status.SUSPENDED)
    post.community.refresh_from_db()
    assert _code(services.set_reaction, actor=reader, target=post, kind="useful", present=True) == (
        "read_only"
    )


def test_121st_reaction_in_the_hour_is_rate_limited(post, reader):
    with mock.patch.object(services, "schedule_recount"):
        for index in range(120):
            services.set_reaction(
                actor=reader, target=post, kind=("useful", "thanks")[index % 2], present=True
            )
        assert _code(
            services.set_reaction, actor=reader, target=post, kind="useful", present=True
        ) == ("rate_limited")


# Bookmarks -------------------------------------------------------------------------


def test_set_bookmark(post, reader):
    assert services.set_bookmark(actor=reader, post=post, present=True) is True
    assert services.set_bookmark(actor=reader, post=post, present=True) is True  # idempotent
    assert Bookmark.objects.filter(user=reader, post=post).count() == 1
    assert services.set_bookmark(actor=reader, post=post, present=False) is False
    assert services.set_bookmark(actor=reader, post=post, present=False) is False
    assert not Bookmark.objects.exists()


def test_bookmark_into_a_collection(post, reader):
    collection = services.create_collection(actor=reader, name="Later")
    services.set_bookmark(actor=reader, post=post, present=True, collection=collection)
    assert Bookmark.objects.get().collection == collection


def test_bookmark_needs_view(make_community, make_post, author, add_member, outsider, reader):
    private = make_community("Private", access_mode=Community.AccessMode.INVITE)
    add_member(private, author)
    secret = make_post(private, author)
    assert _code(services.set_bookmark, actor=outsider, post=secret, present=True) == "forbidden"
    draft = make_post(private, author, status=Post.Status.DRAFT)
    assert _code(services.set_bookmark, actor=reader, post=draft, present=True) == "forbidden"


def test_bookmark_removal_needs_no_read_access(make_community, make_post, author, add_member,
                                              reader):  # fmt: skip
    private = make_community("Private", access_mode=Community.AccessMode.INVITE)
    add_member(private, author)
    add_member(private, reader)
    secret = make_post(private, author)
    assert services.set_bookmark(actor=reader, post=secret, present=True) is True
    CommunityMembership.objects.filter(community=private, user=reader).delete()
    reader = type(reader).objects.get(pk=reader.pk)  # memberships are memoised per instance
    assert services.set_bookmark(actor=reader, post=secret, present=False) is False
    assert not Bookmark.objects.exists()
    assert _code(services.set_bookmark, actor=reader, post=secret, present=True) == "forbidden"


def test_bookmark_open_community_without_membership(post, outsider):
    assert services.set_bookmark(actor=outsider, post=post, present=True) is True


def test_bookmark_into_someone_elses_collection_refused(post, reader, author):
    collection = services.create_collection(actor=author, name="Mine")
    code = _code(
        services.set_bookmark, actor=reader, post=post, collection=collection, present=True
    )
    assert code == "forbidden"


def test_collections_crud(reader, author, post):
    collection = services.create_collection(actor=reader, name="  Reading list ")
    assert collection.name == "Reading list"
    assert _code(services.create_collection, actor=reader, name="reading LIST") == "name_taken"
    # Another user may use the same name.
    services.create_collection(actor=author, name="Reading list")
    assert _code(services.create_collection, actor=reader, name=" ") == "name_required"
    assert _code(services.create_collection, actor=reader, name="x" * 81) == "name_too_long"

    other = services.create_collection(actor=reader, name="Other")
    assert (
        _code(services.rename_collection, actor=reader, collection=other, name="READING list")
        == "name_taken"
    )
    renamed = services.rename_collection(actor=reader, collection=other, name="Archive")
    assert renamed.name == "Archive"
    # Renaming to a different case of its own name is fine.
    assert services.rename_collection(actor=reader, collection=other, name="ARCHIVE").name == (
        "ARCHIVE"
    )
    assert (
        _code(services.rename_collection, actor=author, collection=other, name="Mine")
        == "forbidden"
    )

    services.set_bookmark(actor=reader, post=post, present=True, collection=other)
    assert _code(services.delete_collection, actor=author, collection=other) == "forbidden"
    services.delete_collection(actor=reader, collection=other)
    assert not BookmarkCollection.objects.filter(pk=other.pk).exists()
    assert Bookmark.objects.get(user=reader).collection is None


def test_collections_are_bounded_per_user(reader, settings):
    settings.POSTS_MAX_BOOKMARK_COLLECTIONS = 3
    for index in range(3):
        services.create_collection(actor=reader, name=f"List {index}")
    assert _code(services.create_collection, actor=reader, name="One more") == (
        "too_many_collections"
    )


def test_move_bookmark(post, reader, author):
    services.set_bookmark(actor=reader, post=post, present=True)
    bookmark = Bookmark.objects.get()
    target = services.create_collection(actor=reader, name="Target")
    services.move_bookmark(actor=reader, bookmark=bookmark, collection=target)
    bookmark.refresh_from_db()
    assert bookmark.collection == target
    services.move_bookmark(actor=reader, bookmark=bookmark, collection=None)
    bookmark.refresh_from_db()
    assert bookmark.collection is None
    foreign = services.create_collection(actor=author, name="Foreign")
    assert (
        _code(services.move_bookmark, actor=reader, bookmark=bookmark, collection=foreign)
        == "forbidden"
    )
    assert (
        _code(services.move_bookmark, actor=author, bookmark=bookmark, collection=foreign)
        == "forbidden"
    )


def test_collections_need_an_active_user(reader, make_user):
    inactive = make_user("gone@example.com", status="deactivated")
    assert _code(services.create_collection, actor=inactive, name="X") == "forbidden"
