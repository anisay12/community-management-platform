from unittest import mock

import pytest
from django.core.cache import cache

from audit.models import AuditEvent
from communities.models import Community, CommunityMembership
from core.errors import DomainError
from notifications.models import Notification
from posts import services_interactions as services
from posts.models import Bookmark, BookmarkCollection, Comment, Mention, Post, Reaction

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
            actor=reader, comment=comment, body="Ping @mona.moderator and @ada.author"
        )
    recipients = sorted(_notifications("mention").values_list("recipient", flat=True))
    assert recipients == sorted([moderator.pk, author.pk])


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


# Reactions -------------------------------------------------------------------------


def test_toggle_reaction_on_and_off(post, reader, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        assert services.toggle_reaction(actor=reader, target=post, kind="useful") is True
        assert services.toggle_reaction(actor=reader, target=post, kind="thanks") is True
    post.refresh_from_db()
    assert post.reaction_counts == {"useful": 1, "thanks": 1}
    with django_capture_on_commit_callbacks(execute=True):
        assert services.toggle_reaction(actor=reader, target=post, kind="useful") is False
    post.refresh_from_db()
    assert post.reaction_counts == {"thanks": 1}
    assert Reaction.objects.filter(user=reader).count() == 1


def test_reaction_on_comment(
    post, author, reader, make_comment, django_capture_on_commit_callbacks
):
    comment = make_comment(post, author)
    with django_capture_on_commit_callbacks(execute=True):
        assert services.toggle_reaction(actor=reader, target=comment, kind="insightful")
    comment.refresh_from_db()
    assert comment.reaction_counts == {"insightful": 1}


def test_reaction_on_own_content_refused(post, author):
    assert _code(services.toggle_reaction, actor=author, target=post, kind="useful") == (
        "own_content"
    )


@pytest.mark.parametrize("status", [Post.Status.HIDDEN, Post.Status.ARCHIVED])
def test_reaction_on_hidden_or_archived_post_refused(make_post, community, author, reader, status):
    target = make_post(community, author, status=status)
    code = _code(services.toggle_reaction, actor=reader, target=target, kind="useful")
    assert code in {"invalid_state", "forbidden"}


def test_reaction_on_archived_post_is_invalid_state(make_post, community, author, reader):
    target = make_post(community, author, status=Post.Status.ARCHIVED)
    assert _code(services.toggle_reaction, actor=reader, target=target, kind="useful") == (
        "invalid_state"
    )


def test_reaction_on_hidden_comment_refused(post, author, reader, make_comment):
    comment = make_comment(post, author, status=Comment.Status.HIDDEN, hidden_reason="r")
    code = _code(services.toggle_reaction, actor=reader, target=comment, kind="useful")
    assert code == "invalid_state"


def test_reaction_unknown_kind_and_non_member(post, reader, outsider):
    assert _code(services.toggle_reaction, actor=reader, target=post, kind="love") == (
        "invalid_state"
    )
    assert _code(services.toggle_reaction, actor=outsider, target=post, kind="useful") == (
        "not_member"
    )


def test_reaction_in_read_only_community(post, reader):
    Community.objects.filter(pk=post.community_id).update(status=Community.Status.SUSPENDED)
    post.community.refresh_from_db()
    assert _code(services.toggle_reaction, actor=reader, target=post, kind="useful") == (
        "read_only"
    )


def test_121st_reaction_in_the_hour_is_rate_limited(post, reader):
    with mock.patch.object(services, "schedule_recount"):
        for index in range(120):
            services.toggle_reaction(
                actor=reader, target=post, kind=("useful", "thanks")[index % 2]
            )
        assert _code(services.toggle_reaction, actor=reader, target=post, kind="useful") == (
            "rate_limited"
        )


# Bookmarks -------------------------------------------------------------------------


def test_toggle_bookmark(post, reader):
    assert services.toggle_bookmark(actor=reader, post=post) is True
    assert Bookmark.objects.filter(user=reader, post=post).exists()
    assert services.toggle_bookmark(actor=reader, post=post) is False
    assert not Bookmark.objects.exists()


def test_bookmark_into_a_collection(post, reader):
    collection = services.create_collection(actor=reader, name="Later")
    services.toggle_bookmark(actor=reader, post=post, collection=collection)
    assert Bookmark.objects.get().collection == collection


def test_bookmark_needs_view(make_community, make_post, author, add_member, outsider, reader):
    private = make_community("Private", access_mode=Community.AccessMode.INVITE)
    add_member(private, author)
    secret = make_post(private, author)
    assert _code(services.toggle_bookmark, actor=outsider, post=secret) == "forbidden"
    draft = make_post(private, author, status=Post.Status.DRAFT)
    assert _code(services.toggle_bookmark, actor=reader, post=draft) == "forbidden"


def test_bookmark_open_community_without_membership(post, outsider):
    assert services.toggle_bookmark(actor=outsider, post=post) is True


def test_bookmark_into_someone_elses_collection_refused(post, reader, author):
    collection = services.create_collection(actor=author, name="Mine")
    code = _code(services.toggle_bookmark, actor=reader, post=post, collection=collection)
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

    services.toggle_bookmark(actor=reader, post=post, collection=other)
    assert _code(services.delete_collection, actor=author, collection=other) == "forbidden"
    services.delete_collection(actor=reader, collection=other)
    assert not BookmarkCollection.objects.filter(pk=other.pk).exists()
    assert Bookmark.objects.get(user=reader).collection is None


def test_move_bookmark(post, reader, author):
    services.toggle_bookmark(actor=reader, post=post)
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
