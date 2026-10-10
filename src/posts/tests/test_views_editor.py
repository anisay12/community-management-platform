"""HTTP tests of the post editor (create, edit, preview, mentions) and of the post actions
(publish, delete draft, review, pin, hide, archive, accepted answer, share)."""

import re
import uuid

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from accounts.roles import Role
from audit.models import AuditEvent
from communities.models import Community, CommunityMembership
from communities.roles import CommunityRole
from core.tests.helpers import assert_single_h1
from posts.models import Comment, Post, PostRevision
from taxonomy.models import Tag

pytestmark = pytest.mark.django_db

OPEN, REQUEST, INVITE = Community.AccessMode
S = Post.Status
K = Post.Kind
R = CommunityRole

XSS = '<script>alert("x")</script><img src=x onerror=alert(1)>'


@pytest.fixture
def community(make_community):
    return make_community("Python guild", access_mode=OPEN)


@pytest.fixture
def member_of(make_user, add_member, community):
    """A user with ``role`` in ``community`` (or in ``where``)."""

    def _make(email, role=R.MEMBER, where=None, **extra):
        user = make_user(email, first_name=extra.pop("first_name", "Ann"), **extra)
        add_member(where or community, user, role=role)
        return user

    return _make


@pytest.fixture
def author(member_of):
    return member_of("author@example.com", last_name="Author")


@pytest.fixture
def moderator(member_of):
    return member_of("mod@example.com", R.MODERATOR, last_name="Moderator")


@pytest.fixture
def animator(member_of):
    return member_of("anim@example.com", R.ANIMATOR, last_name="Animator")


def _create(community, kind=None):
    url = reverse("posts:create", args=[community.slug])
    return f"{url}?kind={kind}" if kind else url


def _detail(post):
    return reverse("posts:detail", args=[post.community.slug, post.public_id])


def _edit(post):
    return reverse("posts:edit", args=[post.community.slug, post.public_id])


def _action(name, post):
    return reverse(f"posts:{name}", args=[post.community.slug, post.public_id])


def _form(**extra):
    data = {"kind": K.DISCUSSION, "title": "Hello", "body": "Some *text*", "tags": ""}
    data.update(extra)
    return data


def _toasts(response):
    return [str(message) for message in response.context["messages"]] if response.context else []


# --- Create -----------------------------------------------------------------------------------


def test_create_page_lists_only_allowed_kinds(client, community, author):
    client.force_login(author)
    response = client.get(_create(community))
    assert response.status_code == 200
    assert_single_h1(response)
    html = response.content.decode()
    assert 'value="discussion"' in html and 'value="question"' in html
    assert 'value="announcement"' not in html and 'value="article"' not in html
    assert 'name="version"' not in html
    # The character counter describes the body; the mention list is no combobox popup.
    body = re.search(r"<textarea[^>]*>", html).group(0)
    assert "post-body-counter" in re.search(r'aria-describedby="([^"]+)"', body).group(1)
    assert "aria-expanded" not in body


def test_create_page_offers_every_kind_to_animator(client, community, animator):
    client.force_login(animator)
    html = client.get(_create(community, K.ANNOUNCEMENT)).content.decode()
    for kind in K.values:
        assert f'value="{kind}"' in html
    assert re.search(r'value="announcement"[^>]*selected', html)


@pytest.mark.parametrize(
    ("role", "kind"),
    [
        (R.MEMBER, K.DISCUSSION),
        (R.MEMBER, K.QUESTION),
        (R.CONTRIBUTOR, K.ARTICLE),
        (R.ANIMATOR, K.ANNOUNCEMENT),
    ],
)
def test_create_publishes_each_kind_for_allowed_role(client, community, member_of, role, kind):
    user = member_of("writer@example.com", role)
    client.force_login(user)
    response = client.post(_create(community), _form(kind=kind, action="publish"))
    post = Post.objects.get()
    assert response.status_code == 302 and response.url == _detail(post)
    assert (post.kind, post.status, post.author) == (kind, S.PUBLISHED, user)
    assert "Your post has been published." in _toasts(client.get(response.url))


@pytest.mark.parametrize(("role", "kind"), [(R.MEMBER, K.ARTICLE), (R.CONTRIBUTOR, K.ANNOUNCEMENT)])
def test_create_forbidden_kind_is_403_with_toast(client, community, member_of, role, kind):
    client.force_login(member_of("writer@example.com", role))
    response = client.post(_create(community), _form(kind=kind, action="publish"))
    assert response.status_code == 403
    assert "You cannot publish this kind of post here." in _toasts(response)
    assert not Post.objects.exists()


def test_create_unknown_kind_is_a_form_error(client, community, author):
    client.force_login(author)
    response = client.post(_create(community), _form(kind="poll", action="publish"))
    assert response.status_code == 200
    assert response.context["form"].errors["kind"]
    assert not Post.objects.exists()


def test_create_saves_a_draft(client, community, author):
    client.force_login(author)
    response = client.post(_create(community), _form(action="draft"))
    post = Post.objects.get()
    assert post.status == S.DRAFT
    assert response.url == _detail(post)
    assert "Draft saved." in _toasts(client.get(response.url))


def test_create_submits_for_review(client, community, author, moderator):
    Community.objects.filter(pk=community.pk).update(require_post_review=True)
    client.force_login(author)
    html = client.get(_create(community)).content.decode()
    assert "Submit for review" in html
    response = client.post(_create(community), _form(action="publish"))
    post = Post.objects.get()
    assert post.status == S.PENDING_REVIEW
    assert "Your post has been submitted for review." in _toasts(client.get(response.url))


def test_moderator_publishes_directly_despite_review(client, community, moderator):
    Community.objects.filter(pk=community.pk).update(require_post_review=True)
    client.force_login(moderator)
    assert "Submit for review" not in client.get(_create(community)).content.decode()
    client.post(_create(community), _form(action="publish"))
    assert Post.objects.get().status == S.PUBLISHED


def test_create_with_tags(client, community, author, member_of):
    Tag.objects.create(name="Django", slug="django")
    client.force_login(author)
    html = client.get(_create(community)).content.decode()
    assert '<option value="Django">' in html  # existing tags suggested
    client.post(_create(community), _form(tags="django, ", action="publish"))
    assert [tag.name for tag in Post.objects.get().tags.all()] == ["Django"]


def test_member_cannot_create_new_tag(client, community, author):
    client.force_login(author)
    response = client.post(_create(community), _form(tags="Brand new", action="publish"))
    assert response.status_code == 200
    assert response.context["form"].errors["tags"]
    assert not Post.objects.exists()


def test_contributor_creates_new_tag(client, community, member_of):
    client.force_login(member_of("c@example.com", R.CONTRIBUTOR))
    client.post(_create(community), _form(tags="Brand new", action="publish"))
    assert Tag.objects.filter(name="Brand new").exists()


def test_create_validation_errors_keep_input(client, community, author):
    client.force_login(author)
    response = client.post(_create(community), _form(title="", body="kept body"))
    assert response.status_code == 200
    assert response.context["form"].errors["title"]
    assert "kept body" in response.content.decode()


def test_non_member_gets_join_prompt(client, community, make_user):
    client.force_login(make_user("outsider@example.com"))
    for response in (
        client.get(_create(community)),
        client.post(_create(community), _form(action="publish")),
    ):
        assert response.status_code == 403
        html = response.content.decode()
        assert reverse("communities:join", args=[community.slug]) in html
        assert 'name="title"' not in html
    assert not Post.objects.exists()


@pytest.mark.parametrize("mode", [REQUEST, INVITE])
def test_create_in_invisible_community_is_404(client, make_community, make_user, mode):
    private = make_community("Hidden", access_mode=mode)
    client.force_login(make_user("outsider@example.com"))
    assert client.get(_create(private)).status_code == 404
    assert client.post(_create(private), _form(action="publish")).status_code == 404
    url = reverse("posts:preview", args=[private.slug])
    assert client.post(url, {"body": "x"}).status_code == 404
    url = reverse("posts:mention_suggestions", args=[private.slug])
    assert client.get(url, {"q": "a"}).status_code == 404


def test_create_in_suspended_community_redirects_with_toast(client, community, author):
    Community.objects.filter(pk=community.pk).update(status=Community.Status.SUSPENDED)
    client.force_login(author)
    response = client.get(_create(community))
    assert response.status_code == 302
    assert response.url == reverse("posts:feed", args=[community.slug])


def test_eleventh_post_is_429_with_retry_after(client, community, author):
    client.force_login(author)
    for index in range(10):
        response = client.post(_create(community), _form(title=f"P{index}", action="publish"))
        assert response.status_code == 302
    response = client.post(_create(community), _form(title="P11", action="publish"))
    assert response.status_code == 429
    assert int(response["Retry-After"]) > 0
    assert Post.objects.count() == 10


def test_xss_in_title_and_body_is_inert(client, community, author, make_user):
    client.force_login(author)
    response = client.post(_create(community), _form(title=XSS, body=XSS, action="publish"))
    reader = make_user("reader@example.com")
    client.force_login(reader)
    html = client.get(response.url).content.decode()
    assert "<script>alert" not in html
    assert "<img src=x onerror" not in html
    assert "&lt;script&gt;alert" in html  # the title, escaped


def test_create_button_on_feed_only_for_allowed_users(client, community, author, make_user):
    feed = reverse("posts:feed", args=[community.slug])
    client.force_login(author)
    assert _create(community) in client.get(feed).content.decode()
    client.force_login(make_user("outsider@example.com"))
    assert _create(community) not in client.get(feed).content.decode()


# --- Preview ----------------------------------------------------------------------------------


def test_htmx_preview_renders_body_fragment(client, community, author):
    client.force_login(author)
    url = reverse("posts:preview", args=[community.slug])
    response = client.post(url, {"body": "**bold** " + XSS}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    html = response.content.decode()
    assert "<strong>bold</strong>" in html
    assert "<script" not in html and "<img" not in html
    assert "&lt;script&gt;" in html  # shown as text
    assert "<html" not in html
    assert client.get(url).status_code == 405


def test_no_js_preview_rerenders_editor(client, community, author):
    client.force_login(author)
    response = client.post(_create(community), _form(body="**bold**", action="preview"))
    assert response.status_code == 200
    html = response.content.decode()
    assert "<strong>bold</strong>" in html
    assert not Post.objects.exists()


# --- Edit -------------------------------------------------------------------------------------


def test_author_edits_own_post(client, community, author, make_post):
    post = make_post(community, author, title="Old", body="Old body")
    client.force_login(author)
    response = client.get(_edit(post))
    assert response.status_code == 200
    assert_single_h1(response)
    assert 'name="version" value="1"' in response.content.decode()
    response = client.post(_edit(post), {"title": "New", "body": "New body", "version": 1})
    assert response.status_code == 302 and response.url == _detail(post)
    post.refresh_from_db()
    assert (post.title, post.body, post.version) == ("New", "New body", 2)


def test_moderator_edit_is_audited_and_revised(client, community, author, moderator, make_post):
    post = make_post(community, author, title="Old")
    client.force_login(moderator)
    response = client.post(_edit(post), {"title": "Fixed", "body": "b", "version": 1})
    assert response.status_code == 302
    assert PostRevision.objects.filter(post=post).count() == 1
    assert AuditEvent.objects.filter(action="post.edited_by_moderator").exists()


def test_other_member_cannot_edit(client, community, author, member_of, make_post):
    post = make_post(community, author)
    client.force_login(member_of("other@example.com"))
    assert client.get(_edit(post)).status_code == 403
    response = client.post(_edit(post), {"title": "Hack", "body": "b", "version": 1})
    assert response.status_code == 403
    post.refresh_from_db()
    assert post.title == "A post"


def test_edit_invisible_post_is_404(client, make_community, make_user, add_member, make_post):
    private = make_community("Private", access_mode=INVITE)
    owner = make_user("owner@example.com")
    add_member(private, owner)
    post = make_post(private, owner)
    client.force_login(make_user("outsider@example.com"))
    assert client.get(_edit(post)).status_code == 404
    assert client.post(_action("hide", post), {"reason": "x"}).status_code == 404


def test_stale_version_is_409_and_keeps_text(client, community, author, make_post):
    post = make_post(community, author)
    Post.objects.filter(pk=post.pk).update(version=2)
    client.force_login(author)
    response = client.post(
        _edit(post), {"title": "Mine", "body": "My precious text", "version": 1, "action": "save"}
    )
    assert response.status_code == 409
    html = response.content.decode()
    assert "My precious text" in html
    assert "Reload the latest version" in html
    assert _edit(post) in html
    post.refresh_from_db()
    assert post.title == "A post"


def test_edit_draft_and_publish(client, community, author, make_post):
    post = make_post(community, author, status=S.DRAFT)
    client.force_login(author)
    response = client.post(
        _edit(post), {"title": "Ready", "body": "b", "version": 1, "action": "publish"}
    )
    assert response.status_code == 302
    post.refresh_from_db()
    assert (post.title, post.status) == ("Ready", S.PUBLISHED)


def test_edit_tags_through_update(client, community, author, make_post):
    Tag.objects.create(name="Django", slug="django")
    post = make_post(community, author)
    client.force_login(author)
    client.post(_edit(post), {"title": "T", "body": "b", "version": 1, "tags": "Django"})
    post.refresh_from_db()
    assert post.version == 2
    assert [tag.name for tag in post.tags.all()] == ["Django"]


# --- Action buttons ---------------------------------------------------------------------------


def _detail_html(client, user, post):
    client.force_login(user)
    return client.get(_detail(post)).content.decode()


def test_action_buttons_hidden_from_plain_reader(client, community, author, member_of, make_post):
    post = make_post(community, author, kind=K.QUESTION)
    html = _detail_html(client, member_of("reader@example.com"), post)
    for name in ("pin", "hide", "archive", "accept_answer", "publish", "delete_draft"):
        assert _action(name, post) not in html, name
    assert _edit(post) not in html


def test_author_sees_edit_and_draft_actions(client, community, author, make_post):
    post = make_post(community, author, status=S.DRAFT)
    html = _detail_html(client, author, post)
    assert _edit(post) in html
    assert _action("publish", post) in html
    assert _action("delete_draft", post) in html
    assert _action("hide", post) not in html and _action("pin", post) not in html


def test_moderator_sees_moderation_actions(client, community, author, moderator, make_post):
    post = make_post(community, author)
    html = _detail_html(client, moderator, post)
    assert _action("hide", post) in html and _action("archive", post) in html
    assert _action("pin", post) not in html  # pinning is for facilitators
    assert 'id="hide-post-modal"' in html and 'name="reason"' in html


def test_animator_sees_pin_and_unpin(client, community, author, animator, make_post):
    post = make_post(community, author)
    assert _action("pin", post) in _detail_html(client, animator, post)
    Post.objects.filter(pk=post.pk).update(pinned_at=post.published_at)
    html = _detail_html(client, animator, post)
    assert _action("unpin", post) in html and _action("pin", post) not in html


def test_reviewer_sees_review_buttons(client, community, author, moderator, make_post):
    post = make_post(community, author, status=S.PENDING_REVIEW)
    html = _detail_html(client, moderator, post)
    assert _action("review_decide", post) in html
    assert 'value="approve"' in html and 'id="reject-post-modal"' in html
    assert _action("review_decide", post) not in _detail_html(client, author, post)


def test_read_only_community_offers_send_back_but_not_approve(
    client, community, author, moderator, make_post
):
    post = make_post(community, author, status=S.PENDING_REVIEW)
    Community.objects.filter(pk=community.pk).update(status=Community.Status.SUSPENDED)
    html = _detail_html(client, moderator, post)
    assert 'id="reject-post-modal"' in html
    assert 'value="approve"' not in html


def test_hidden_post_shows_unhide_to_moderator(client, community, author, moderator, make_post):
    post = make_post(community, author, status=S.HIDDEN, hidden_reason="Spam")
    html = _detail_html(client, moderator, post)
    assert _action("unhide", post) in html and _action("hide", post) not in html


def test_get_on_action_urls_is_405(client, community, author, moderator, make_post):
    post = make_post(community, author)
    client.force_login(moderator)
    names = (
        "publish",
        "delete_draft",
        "pin",
        "unpin",
        "hide",
        "unhide",
        "archive",
        "accept_answer",
        "review_decide",
    )
    for name in names:
        assert client.get(_action(name, post)).status_code == 405, name
    assert client.get(reverse("posts:preview", args=[community.slug])).status_code == 405


# --- Actions ----------------------------------------------------------------------------------


def test_publish_draft_action(client, community, author, make_post):
    post = make_post(community, author, status=S.DRAFT)
    client.force_login(author)
    response = client.post(_action("publish", post))
    assert response.status_code == 302 and response.url == _detail(post)
    post.refresh_from_db()
    assert post.status == S.PUBLISHED


def test_publish_someone_elses_draft_is_404(client, community, author, member_of, make_post):
    post = make_post(community, author, status=S.DRAFT)
    client.force_login(member_of("other@example.com"))
    assert client.post(_action("publish", post)).status_code == 404


def test_delete_draft_needs_confirmation(client, community, author, make_post):
    post = make_post(community, author, status=S.DRAFT)
    client.force_login(author)
    response = client.post(_action("delete_draft", post))
    assert response.status_code == 200  # no-JS confirmation page
    assert Post.objects.filter(pk=post.pk).exists()
    response = client.post(_action("delete_draft", post), {"confirmed": "1"})
    assert response.status_code == 302
    assert response.url == reverse("posts:feed", args=[community.slug])
    assert not Post.objects.filter(pk=post.pk).exists()


def test_pin_and_unpin(client, community, author, animator, make_post):
    post = make_post(community, author)
    client.force_login(animator)
    response = client.post(_action("pin", post))
    assert response.status_code == 302
    post.refresh_from_db()
    assert post.pinned_at is not None
    assert "The post has been pinned." in _toasts(client.get(response.url))
    client.post(_action("unpin", post))
    post.refresh_from_db()
    assert post.pinned_at is None


def test_pin_by_member_is_403(client, community, author, member_of, make_post):
    post = make_post(community, author)
    client.force_login(member_of("m@example.com"))
    assert client.post(_action("pin", post)).status_code == 403


def test_pin_limit_shows_toast(client, community, author, animator, make_post):
    for _index in range(3):
        make_post(community, author, pinned_at=community.created_at)
    post = make_post(community, author)
    client.force_login(animator)
    response = client.post(_action("pin", post))
    assert response.status_code == 302
    assert any("At most 3 posts" in toast for toast in _toasts(client.get(response.url)))


def test_hide_requires_reason_and_is_audited(client, community, author, moderator, make_post):
    post = make_post(community, author)
    client.force_login(moderator)
    response = client.post(_action("hide", post))
    assert response.status_code == 200  # confirmation page with the reason field
    assert 'name="reason"' in response.content.decode()
    response = client.post(_action("hide", post), {"confirmed": "1", "reason": "  "})
    assert response.status_code == 200
    assert response.context["form"].errors
    response = client.post(_action("hide", post), {"confirmed": "1", "reason": "Off-topic"})
    assert response.status_code == 302
    post.refresh_from_db()
    assert (post.status, post.hidden_reason) == (S.HIDDEN, "Off-topic")
    assert AuditEvent.objects.filter(action="post.hidden").count() == 1


def test_hide_by_member_is_403(client, community, author, member_of, make_post):
    post = make_post(community, author)
    client.force_login(member_of("m@example.com"))
    assert client.post(_action("hide", post)).status_code == 403
    response = client.post(_action("hide", post), {"confirmed": "1", "reason": "x"})
    assert response.status_code == 403


def test_unhide(client, community, author, moderator, make_post):
    post = make_post(community, author)
    client.force_login(moderator)
    client.post(_action("hide", post), {"confirmed": "1", "reason": "Spam"})
    response = client.post(_action("unhide", post))
    assert response.status_code == 302
    post.refresh_from_db()
    assert post.status == S.PUBLISHED


def test_archive_with_confirmation(client, community, author, moderator, make_post):
    post = make_post(community, author)
    client.force_login(moderator)
    assert client.post(_action("archive", post)).status_code == 200
    response = client.post(_action("archive", post), {"confirmed": "1"})
    assert response.status_code == 302
    post.refresh_from_db()
    assert post.status == S.ARCHIVED


def test_archive_twice_shows_error_toast(client, community, author, moderator, make_post):
    post = make_post(community, author, status=S.ARCHIVED)
    client.force_login(moderator)
    response = client.post(_action("archive", post), {"confirmed": "1"})
    assert response.status_code == 302
    assert "This action is not possible in the current state." in _toasts(client.get(response.url))


def test_review_approve_and_reject(client, community, author, moderator, make_post):
    first = make_post(community, author, status=S.PENDING_REVIEW, title="First")
    second = make_post(community, author, status=S.PENDING_REVIEW, title="Second")
    client.force_login(moderator)
    response = client.post(_action("review_decide", first), {"decision": "approve"})
    assert response.status_code == 302
    first.refresh_from_db()
    assert first.status == S.PUBLISHED
    response = client.post(_action("review_decide", second), {"decision": "reject"})
    assert response.status_code == 200  # no-JS page asking for the note
    assert_single_h1(response)
    response = client.post(
        _action("review_decide", second), {"decision": "reject", "confirmed": "1", "note": ""}
    )
    assert response.status_code == 200 and response.context["form"].errors
    response = client.post(
        _action("review_decide", second),
        {"decision": "reject", "confirmed": "1", "note": "Too vague"},
    )
    assert response.status_code == 302
    second.refresh_from_db()
    assert (second.status, second.review_note) == (S.DRAFT, "Too vague")


def test_review_by_author_is_403(client, community, author, make_post):
    post = make_post(community, author, status=S.PENDING_REVIEW)
    client.force_login(author)
    response = client.post(_action("review_decide", post), {"decision": "approve"})
    assert response.status_code == 403


@pytest.mark.parametrize(
    "data",
    [
        {"decision": "reject"},
        {"decision": "reject", "confirmed": "1"},
        {"decision": "reject", "confirmed": "1", "note": "Too vague"},
        {"decision": "approve"},
    ],
)
def test_review_of_a_post_no_longer_pending_shows_error(
    client, community, author, moderator, make_post, data
):
    post = make_post(community, author)  # already published (e.g. decided in another tab)
    client.force_login(moderator)
    response = client.post(_action("review_decide", post), data)
    assert response.status_code == 302
    assert response.url == _detail(post)
    assert "This action is not possible in the current state." in _toasts(client.get(response.url))
    post.refresh_from_db()
    assert post.status == S.PUBLISHED


def test_review_unknown_decision_is_400(client, community, author, moderator, make_post):
    post = make_post(community, author, status=S.PENDING_REVIEW)
    client.force_login(moderator)
    assert client.post(_action("review_decide", post), {"decision": "x"}).status_code == 400


def _accept_button(comment):
    return f'name="comment" value="{comment.public_id}"'


def test_accept_and_clear_answer(client, community, author, member_of, make_post, make_comment):
    post = make_post(community, author, kind=K.QUESTION)
    answer = make_comment(post, member_of("helper@example.com"), body="Try this")
    client.force_login(author)
    html = client.get(_detail(post)).content.decode()
    assert _action("accept_answer", post) in html and _accept_button(answer) in html
    response = client.post(_action("accept_answer", post), {"comment": str(answer.public_id)})
    assert response.status_code == 302
    post.refresh_from_db()
    assert post.accepted_answer == answer
    client.post(_action("accept_answer", post), {"clear": "1"})
    post.refresh_from_db()
    assert post.accepted_answer is None


def test_accept_button_on_each_eligible_comment(
    client, community, author, member_of, make_post, make_comment
):
    """One "Accept this answer" button per visible top-level comment, except the accepted
    one; none on replies, none for a reader who may not decide."""
    post = make_post(community, author, kind=K.QUESTION)
    helper = member_of("helper@example.com")
    first = make_comment(post, helper, body="First idea")
    second = make_comment(post, helper, body="Second idea")
    reply = make_comment(post, author, parent=first, body="Thanks")
    hidden = make_comment(post, helper, status=Comment.Status.HIDDEN, hidden_reason="Spam")
    Post.objects.filter(pk=post.pk).update(accepted_answer=second)
    client.force_login(author)
    html = client.get(_detail(post)).content.decode()
    assert _accept_button(first) in html
    for comment in (second, reply, hidden):
        assert _accept_button(comment) not in html
    assert html.count("Accept this answer") == 1
    assert "Clear the accepted answer" in html
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "the highlighted accepted answer duplicates ids"
    client.force_login(helper)
    assert "Accept this answer" not in client.get(_detail(post)).content.decode()
    discussion = make_post(community, author)
    make_comment(discussion, helper)
    client.force_login(author)
    assert "Accept this answer" not in client.get(_detail(discussion)).content.decode()


def test_accept_answer_by_reader_is_403_and_unknown_comment_404(
    client, community, author, member_of, make_post, make_comment
):
    post = make_post(community, author, kind=K.QUESTION)
    answer = make_comment(post, author)
    client.force_login(member_of("reader@example.com"))
    url = _action("accept_answer", post)
    assert client.post(url, {"comment": str(answer.public_id)}).status_code == 403
    # Rights first: a reader learns nothing about which comments exist.
    assert client.post(url, {"comment": str(uuid.uuid4())}).status_code == 403
    assert client.post(url, {"comment": "not-a-uuid"}).status_code == 403
    client.force_login(author)
    other = make_comment(make_post(community, author, kind=K.QUESTION), author)
    assert client.post(url, {"comment": str(other.public_id)}).status_code == 404
    assert client.post(url, {"comment": "not-a-uuid"}).status_code == 404
    assert Comment.objects.count() == 2


# --- Share ------------------------------------------------------------------------------------


def test_share_prefills_title_and_creates_discussion(
    client, community, author, make_community, member_of, make_post
):
    target = make_community("Data guild")
    sharer = member_of("sharer@example.com")
    member_of("sharer2@example.com", where=target)
    CommunityMembership.objects.create(community=target, user=sharer, role=R.MEMBER)
    post = make_post(community, author, title="Original title")
    client.force_login(sharer)
    assert _action("share", post) in client.get(_detail(post)).content.decode()
    response = client.get(_action("share", post))
    assert response.status_code == 200
    assert_single_h1(response)
    html = response.content.decode()
    assert 'value="Original title"' in html and "data-guild" in html
    response = client.post(
        _action("share", post),
        {"target": "data-guild", "title": "Worth a read", "comment": "See this"},
    )
    shared = Post.objects.get(community=target)
    assert response.status_code == 302 and response.url == _detail(shared)
    assert (shared.title, shared.body, shared.shared_from) == ("Worth a read", "See this", post)


def test_share_requires_title_and_known_target(
    client, community, author, make_community, member_of, make_post
):
    target = make_community("Data guild")
    sharer = member_of("sharer@example.com")
    CommunityMembership.objects.create(community=target, user=sharer, role=R.MEMBER)
    post = make_post(community, author)
    client.force_login(sharer)
    response = client.post(_action("share", post), {"target": "data-guild", "title": ""})
    assert response.status_code == 200 and response.context["form"].errors["title"]
    response = client.post(_action("share", post), {"target": "nowhere", "title": "T"})
    assert response.status_code == 200 and response.context["form"].errors["target"]
    assert Post.objects.count() == 1


def test_share_button_hidden_without_target(client, community, author, member_of, make_post):
    post = make_post(community, author)
    html = _detail_html(client, member_of("reader@example.com"), post)
    assert _action("share", post) not in html


# --- Mention suggestions ----------------------------------------------------------------------


def test_mention_suggestions_lists_matching_members(client, community, member_of, make_user):
    viewer = member_of("viewer@example.com", first_name="Zed", last_name="Viewer")
    for index in range(10):
        member_of(f"j{index}@example.com", first_name="Jean", last_name=f"Dupont{index}")
    make_user("jean.out@example.com", first_name="Jean", last_name="Outsider")
    client.force_login(viewer)
    url = reverse("posts:mention_suggestions", args=[community.slug])
    response = client.get(url, {"q": "jean.dup"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    html = response.content.decode()
    assert html.count('role="option"') == 8
    assert "outsider" not in html.lower()
    assert "@example.com" not in html
    assert client.get(url, {"q": ""}).content.decode().count('role="option"') == 0


def test_mention_suggestions_for_non_member_is_403(client, community, make_user):
    client.force_login(make_user("outsider@example.com"))
    url = reverse("posts:mention_suggestions", args=[community.slug])
    assert client.get(url, {"q": "a"}).status_code == 403


def test_mention_suggestions_for_functional_admin_writing_an_announcement(
    client, community, member_of, make_user, verified_login
):
    """The functional administrator publishes announcements without being a member (framing
    § 4.4): the editor's suggestions work for them too, and still list members only."""
    member_of("jean@example.com", first_name="Jean", last_name="Dupont")
    admin = make_user("admin@example.com", first_name="Jean", last_name="Admin")
    admin.groups.add(Group.objects.get(name=Role.FUNCTIONAL_ADMIN))
    verified_login(client, admin)
    url = reverse("posts:mention_suggestions", args=[community.slug])
    html = client.get(url, {"q": "jean"}).content.decode()
    assert "@jean.dupont" in html
    assert "jean.admin" not in html


# --- Edge cases ---------------------------------------------------------------------------------


def test_edit_form_errors_preview_and_tag_refusal(client, community, author, make_post):
    post = make_post(community, author)
    client.force_login(author)
    response = client.post(_edit(post), {"title": "", "body": "b", "version": 1})
    assert response.status_code == 200 and response.context["form"].errors["title"]
    response = client.post(
        _edit(post), {"title": "T", "body": "**new**", "version": 1, "action": "preview"}
    )
    assert "<strong>new</strong>" in response.content.decode()
    response = client.post(_edit(post), {"title": "T", "body": "b", "version": 1, "tags": "Nope"})
    assert response.status_code == 200 and response.context["form"].errors["tags"]
    post.refresh_from_db()
    assert post.version == 1


def test_create_closed_to_technical_admin_is_403(client, community, make_user, verified_login):
    staff = make_user("tech@example.com")
    staff.groups.add(Group.objects.get(name=Role.TECHNICAL_ADMIN))
    verified_login(client, staff)
    assert client.get(_create(community)).status_code == 403


def test_share_title_too_long_is_a_form_error(
    client, community, author, make_community, member_of, make_post
):
    target = make_community("Data guild")
    sharer = member_of("sharer@example.com")
    CommunityMembership.objects.create(community=target, user=sharer, role=R.MEMBER)
    post = make_post(community, author)
    client.force_login(sharer)
    response = client.post(_action("share", post), {"target": "data-guild", "title": "x" * 201})
    assert response.status_code == 200 and response.context["form"].errors["title"]
