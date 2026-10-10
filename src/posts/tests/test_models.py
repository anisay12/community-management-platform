"""Database constraints of the posts models."""

import pytest
from django.db import IntegrityError, transaction

from posts.models import (
    Bookmark,
    BookmarkCollection,
    ContentReport,
    Mention,
    Post,
    Reaction,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def community(make_community):
    return make_community()


@pytest.fixture
def post(make_post, community, active_user):
    return make_post(community, active_user)


@pytest.fixture
def comment(make_comment, post, active_user):
    return make_comment(post, active_user)


@pytest.fixture
def other(make_user):
    return make_user("bob@example.com", first_name="Bob")


def _fails(**kwargs):
    model = kwargs.pop("model")
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(**kwargs)


def test_str_and_defaults(post, comment):
    assert str(post) == "A post"
    assert post.version == 1
    assert post.reaction_counts == {}
    assert post.body_html == "<p>Some <strong>body</strong></p>\n"
    assert str(comment).endswith(f"on {post.pk}")


def test_reaction_unique_per_user_target_kind(post, comment, other):
    Reaction.objects.create(user=other, post=post, kind=Reaction.Kind.USEFUL)
    Reaction.objects.create(user=other, post=post, kind=Reaction.Kind.THANKS)
    Reaction.objects.create(user=other, comment=comment, kind=Reaction.Kind.USEFUL)
    _fails(model=Reaction, user=other, post=post, kind=Reaction.Kind.USEFUL)
    _fails(model=Reaction, user=other, comment=comment, kind=Reaction.Kind.USEFUL)


@pytest.mark.parametrize("model", [Reaction, Mention, ContentReport])
def test_exactly_one_target(model, post, comment, other, community):
    base = {
        Reaction: {"user": other, "kind": "useful"},
        Mention: {"mentioned_user": other},
        ContentReport: {"reporter": other, "community": community, "reason": "spam"},
    }[model]
    _fails(model=model, **base)
    _fails(model=model, post=post, comment=comment, **base)


def test_bookmark_needs_a_target_and_is_unique(post, other):
    _fails(model=Bookmark, user=other)
    Bookmark.objects.create(user=other, post=post)
    _fails(model=Bookmark, user=other, post=post)


def test_collection_name_unique_per_user_case_insensitive(other, active_user):
    BookmarkCollection.objects.create(user=other, name="Reading")
    BookmarkCollection.objects.create(user=active_user, name="Reading")
    _fails(model=BookmarkCollection, user=other, name="READING")


def test_mention_unique_per_source(post, comment, other):
    Mention.objects.create(post=post, mentioned_user=other)
    Mention.objects.create(comment=comment, mentioned_user=other)
    _fails(model=Mention, post=post, mentioned_user=other)
    _fails(model=Mention, comment=comment, mentioned_user=other)


@pytest.mark.parametrize("target", ["post", "comment"])
def test_one_open_report_per_reporter_and_target(target, post, comment, other, community):
    kwargs = {target: post if target == "post" else comment}
    first = ContentReport.objects.create(
        reporter=other, community=community, reason="spam", **kwargs
    )
    _fails(model=ContentReport, reporter=other, community=community, reason="other", **kwargs)
    first.status = ContentReport.Status.DISMISSED
    first.save()
    ContentReport.objects.create(reporter=other, community=community, reason="other", **kwargs)


def test_accepted_answer_only_on_questions(post, comment, make_post, community, active_user):
    post.accepted_answer = comment
    with pytest.raises(IntegrityError), transaction.atomic():
        post.save()
    question = make_post(community, active_user, kind=Post.Kind.QUESTION)
    question.accepted_answer = comment
    question.save()
