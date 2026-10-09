import pytest

from audit.models import AuditEvent
from core.errors import DomainError
from posts.hiding import mark_hidden, mark_visible
from posts.models import Comment, Post

pytestmark = pytest.mark.django_db


@pytest.fixture
def community(make_community):
    return make_community()


@pytest.fixture
def post(make_post, community, active_user):
    return make_post(community, active_user, status=Post.Status.ARCHIVED)


def test_hiding_a_post_stores_and_restores_its_status(post, functional_admin, community):
    mark_hidden(post, actor=functional_admin, reason="Off topic")
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN
    assert post.status_before_hidden == Post.Status.ARCHIVED
    assert post.hidden_by == functional_admin
    assert post.hidden_reason == "Off topic"
    event = AuditEvent.objects.get(action="post.hidden")
    assert event.community_id == community.pk
    assert event.changes == {"reason": "Off topic"}
    assert event.actor == functional_admin

    mark_visible(post, actor=functional_admin)
    post.refresh_from_db()
    assert post.status == Post.Status.ARCHIVED
    assert (post.status_before_hidden, post.hidden_reason, post.hidden_by) == ("", "", None)
    assert AuditEvent.objects.filter(action="post.unhidden", community_id=community.pk).exists()


def test_automatic_hiding_of_a_comment(make_comment, post, active_user, community):
    comment = make_comment(post, active_user)
    mark_hidden(comment, actor=None, reason="", automatic=True)
    comment.refresh_from_db()
    assert comment.status == Comment.Status.HIDDEN
    assert comment.hidden_by is None
    assert comment.hidden_reason == "automatic"
    event = AuditEvent.objects.get(action="comment.auto_hidden")
    assert event.actor is None and event.community_id == community.pk
    mark_visible(comment)
    comment.refresh_from_db()
    assert comment.status == Comment.Status.VISIBLE
    assert AuditEvent.objects.filter(action="comment.unhidden").exists()


def test_reason_required_and_state_checked(post, functional_admin):
    with pytest.raises(DomainError) as error:
        mark_hidden(post, actor=functional_admin, reason="  ")
    assert error.value.code == "reason_required"
    with pytest.raises(DomainError) as error:
        mark_visible(post)
    assert error.value.code == "invalid_state"
    mark_hidden(post, actor=functional_admin, reason="Spam")
    with pytest.raises(DomainError) as error:
        mark_hidden(post, actor=functional_admin, reason="Spam")
    assert error.value.code == "invalid_state"


def test_audit_is_rolled_back_with_the_change(post, functional_admin, monkeypatch):
    from posts import hiding

    def boom(**kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(hiding, "record", boom)
    with pytest.raises(RuntimeError):
        mark_hidden(post, actor=functional_admin, reason="Spam")
    post.refresh_from_db()
    assert post.status == Post.Status.ARCHIVED
