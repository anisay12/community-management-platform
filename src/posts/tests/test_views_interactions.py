"""HTTP tests of the interaction pages: comments, reactions, bookmarks and reports.

Every action answers a partial to an HTMX request and redirects a plain request (PRG).
"""

from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse

from communities.models import Community
from communities.roles import CommunityRole
from core.tests.helpers import assert_single_h1
from posts import services_interactions as services
from posts.models import Bookmark, BookmarkCollection, Comment, ContentReport, Post, Reaction

pytestmark = pytest.mark.django_db

OPEN, REQUEST = Community.AccessMode.OPEN, Community.AccessMode.REQUEST
HTMX = {"HX-Request": "true"}


@pytest.fixture
def community(make_community):
    return make_community("Python guild", access_mode=OPEN)


@pytest.fixture
def author(make_user, community, add_member):
    user = make_user("author@example.com", first_name="Ann", last_name="Author")
    add_member(community, user)
    return user


@pytest.fixture
def member(make_user, community, add_member):
    user = make_user("member@example.com", first_name="Mia", last_name="Member")
    add_member(community, user)
    return user


@pytest.fixture
def moderator(make_user, community, add_member):
    user = make_user("mod@example.com", first_name="Mo", last_name="Derator")
    add_member(community, user, CommunityRole.MODERATOR)
    return user


@pytest.fixture
def outsider(make_user):
    return make_user("outsider@example.com", first_name="Out", last_name="Sider")


@pytest.fixture
def post(make_post, community, author):
    return make_post(community, author, title="Hello")


@pytest.fixture
def member_client(client, member):
    client.force_login(member)
    return client


def _detail(post):
    return reverse("posts:detail", args=[post.community.slug, post.public_id])


def _comment_create(post):
    return reverse("posts:comment_create", args=[post.community.slug, post.public_id])


def _react(target, obj):
    return reverse("posts:react", args=[target, obj.public_id])


def _bookmark(post):
    return reverse("posts:bookmark_toggle", args=[post.community.slug, post.public_id])


def _report(target, obj):
    return reverse("posts:report", args=[target, obj.public_id])


def _messages(response):
    return [str(message) for message in response.wsgi_request._messages]


# --- Comments -------------------------------------------------------------------------------


def test_comment_create_plain_redirects_to_the_new_comment(member_client, post):
    response = member_client.post(_comment_create(post), {"body": "Nice **post**"})
    comment = Comment.objects.get()
    assert response.status_code == 302
    assert response["Location"] == f"{_detail(post)}#comment-{comment.public_id}"
    assert comment.body == "Nice **post**" and comment.parent is None


def test_comment_create_htmx_returns_the_comment_and_a_fresh_form(member_client, post):
    response = member_client.post(_comment_create(post), {"body": "Nice **post**"}, headers=HTMX)
    html = response.content.decode()
    assert response.status_code == 200
    assert "<strong>post</strong>" in html
    assert 'id="comment-form"' in html
    assert "<html" not in html


def test_reply_htmx_returns_the_whole_top_level_comment(member_client, post, author, make_comment):
    top = make_comment(post, author, body="Top")
    reply = make_comment(post, author, parent=top, body="First reply")
    response = member_client.post(
        _comment_create(post),
        {"body": "Reply to the reply", "parent": str(reply.public_id)},
        headers=HTMX,
    )
    new = Comment.objects.get(body="Reply to the reply")
    assert new.parent == top  # flattened to two levels
    html = response.content.decode()
    assert f'id="comment-{top.public_id}"' in html
    assert "First reply" in html and "Reply to the reply" in html


def test_reply_plain_redirects(member_client, post, author, make_comment):
    top = make_comment(post, author)
    response = member_client.post(
        _comment_create(post), {"body": "Agreed", "parent": str(top.public_id)}
    )
    new = Comment.objects.get(body="Agreed")
    assert response["Location"] == f"{_detail(post)}#comment-{new.public_id}"


def test_reply_to_comment_of_another_post_is_404(
    member_client, post, community, author, make_post, make_comment
):
    other = make_comment(make_post(community, author), author)
    response = member_client.post(
        _comment_create(post), {"body": "x", "parent": str(other.public_id)}
    )
    assert response.status_code == 404


def test_empty_comment_is_an_error_toast(member_client, post):
    response = member_client.post(_comment_create(post), {"body": "   "}, follow=True)
    assert "Write something first." in response.content.decode()
    assert not Comment.objects.exists()
    htmx = member_client.post(_comment_create(post), {"body": ""}, headers=HTMX)
    assert (htmx.status_code, htmx["HX-Redirect"]) == (204, _detail(post))


def test_too_long_comment_is_refused(member_client, post):
    response = member_client.post(_comment_create(post), {"body": "x" * 10_001}, follow=True)
    assert "10000" in response.content.decode() or "10,000" in response.content.decode()
    assert not Comment.objects.exists()


def test_61st_comment_in_the_hour_is_429(member_client, member, post):
    with mock.patch.object(services, "schedule_recount"):
        for index in range(60):
            services.add_comment(actor=member, post=post, body=f"Comment {index}")
    response = member_client.post(_comment_create(post), {"body": "One too many"})
    assert response.status_code == 429
    assert int(response["Retry-After"]) > 0


def test_comment_pages_require_post_and_login(client, member_client, post):
    assert member_client.get(_comment_create(post)).status_code == 405
    client.logout()
    assert client.post(_comment_create(post), {"body": "x"}).status_code == 302
    assert not Comment.objects.exists()


def test_invisible_post_is_404_for_every_action(
    client, make_user, make_community, add_member, make_post, make_comment
):
    private = make_community("Private", access_mode=REQUEST)
    owner = make_user("owner@example.com")
    add_member(private, owner)
    post = make_post(private, owner)
    comment = make_comment(post, owner)
    client.force_login(make_user("stranger@example.com"))
    for url, data in (
        (_comment_create(post), {"body": "x"}),
        (_react("post", post), {"kind": "useful"}),
        (_react("comment", comment), {"kind": "useful"}),
        (_bookmark(post), {}),
        (_report("post", post), {"reason": "spam"}),
        (_report("comment", comment), {"reason": "spam"}),
        (reverse("posts:comment_edit", args=[comment.public_id]), {"body": "x"}),
        (reverse("posts:comment_hide", args=[comment.public_id]), {"reason": "x"}),
        (reverse("posts:comment_unhide", args=[comment.public_id]), {}),
    ):
        assert client.post(url, data).status_code == 404, url
    assert client.get(_report("post", post)).status_code == 404


def test_unknown_reaction_or_report_target_is_404(member_client, post):
    assert member_client.post(_react("document", post), {"kind": "useful"}).status_code == 404
    assert member_client.get(_report("document", post)).status_code == 404


def test_hidden_comment_is_404_to_other_readers(member_client, post, author, make_comment):
    hidden = make_comment(post, author, status=Comment.Status.HIDDEN)
    assert member_client.post(_report("comment", hidden), {"reason": "spam"}).status_code == 404


def test_detail_shows_comment_form_and_reply_for_members(member_client, post, author, make_comment):
    comment = make_comment(post, author)
    html = member_client.get(_detail(post)).content.decode()
    assert f'action="{_comment_create(post)}"' in html
    assert f'name="parent" value="{comment.public_id}"' in html
    assert 'maxlength="10000"' in html
    assert "Reply" in html


def test_non_member_of_open_community_sees_join_prompt(
    client, outsider, post, author, make_comment
):
    make_comment(post, author)
    client.force_login(outsider)
    html = client.get(_detail(post)).content.decode()
    assert "Join this community to comment and react." in html
    assert reverse("communities:join", args=[post.community.slug]) in html
    assert f'action="{_comment_create(post)}"' not in html
    assert 'name="kind"' not in html  # reactions shown read-only
    assert _bookmark(post) in html and _report("post", post) in html


def test_non_member_cannot_comment_or_react_but_can_report_and_bookmark(
    client, outsider, post, author, make_comment
):
    comment = make_comment(post, author)
    client.force_login(outsider)
    response = client.post(_comment_create(post), {"body": "Hi"}, follow=True)
    assert "Join this community to take part." in response.content.decode()
    response = client.post(_react("post", post), {"kind": "useful"}, follow=True)
    assert "Join this community to take part." in response.content.decode()
    assert not Comment.objects.exclude(pk=comment.pk).exists() and not Reaction.objects.exists()
    assert client.post(_bookmark(post)).status_code == 302
    assert Bookmark.objects.filter(user=outsider, post=post).exists()
    client.post(_report("comment", comment), {"reason": "spam"})
    assert ContentReport.objects.filter(reporter=outsider, comment=comment).exists()


def test_edit_own_comment_plain_and_htmx(client, post, member, make_comment):
    comment = make_comment(post, member, body="Before")
    url = reverse("posts:comment_edit", args=[comment.public_id])
    client.force_login(member)
    response = client.post(url, {"body": "After *edit*"})
    assert response["Location"] == f"{_detail(post)}#comment-{comment.public_id}"
    comment.refresh_from_db()
    assert comment.body == "After *edit*"
    response = client.post(url, {"body": "Again"}, headers=HTMX)
    html = response.content.decode()
    assert response.status_code == 200 and f'id="comment-{comment.public_id}"' in html
    assert "Again" in html and "<html" not in html


def test_edit_form_shown_only_on_own_comments(client, post, member, author, make_comment):
    own = make_comment(post, member)
    other = make_comment(post, author)
    client.force_login(member)
    html = client.get(_detail(post)).content.decode()
    assert reverse("posts:comment_edit", args=[own.public_id]) in html
    assert reverse("posts:comment_edit", args=[other.public_id]) not in html


def test_edit_someone_elses_comment_is_refused(member_client, post, author, make_comment):
    comment = make_comment(post, author, body="Original")
    url = reverse("posts:comment_edit", args=[comment.public_id])
    response = member_client.post(url, {"body": "Hacked"}, follow=True)
    assert "You are not allowed to perform this action." in response.content.decode()
    comment.refresh_from_db()
    assert comment.body == "Original"


def test_moderator_hides_and_unhides_a_comment(client, post, author, moderator, make_comment):
    comment = make_comment(post, author)
    client.force_login(moderator)
    hide = reverse("posts:comment_hide", args=[comment.public_id])
    assert (
        reverse("posts:comment_hide", args=[comment.public_id])
        in client.get(_detail(post)).content.decode()
    )
    response = client.post(hide, {"reason": "Off topic"})
    assert response["Location"] == f"{_detail(post)}#comment-{comment.public_id}"
    comment.refresh_from_db()
    assert (comment.status, comment.hidden_reason) == (Comment.Status.HIDDEN, "Off topic")
    unhide = reverse("posts:comment_unhide", args=[comment.public_id])
    response = client.post(unhide, headers=HTMX)
    html = response.content.decode()
    assert response.status_code == 200 and f'id="comment-{comment.public_id}"' in html
    comment.refresh_from_db()
    assert comment.status == Comment.Status.VISIBLE
    response = client.post(hide, {"reason": "Spam again"}, headers=HTMX)
    assert response.status_code == 200 and "Spam again" in response.content.decode()


def test_hide_needs_a_reason_and_a_moderator(member_client, post, author, moderator,
                                             make_comment):  # fmt: skip
    comment = make_comment(post, author)
    hide = reverse("posts:comment_hide", args=[comment.public_id])
    response = member_client.post(hide, {"reason": "x"}, follow=True)
    assert "You are not allowed to perform this action." in response.content.decode()
    assert hide not in member_client.get(_detail(post)).content.decode()
    client = Client()
    client.force_login(moderator)
    response = client.post(hide, {"reason": "  "}, follow=True)
    assert response.status_code == 200
    comment.refresh_from_db()
    assert comment.status == Comment.Status.VISIBLE


# --- Reactions ------------------------------------------------------------------------------


def test_react_plain_redirects_and_toggles(member_client, member, post):
    response = member_client.post(_react("post", post), {"kind": "useful"})
    assert response["Location"] == _detail(post)
    assert Reaction.objects.filter(user=member, post=post, kind="useful").exists()
    member_client.post(_react("post", post), {"kind": "useful"})
    assert not Reaction.objects.exists()


def test_react_htmx_returns_the_bar_with_optimistic_count(member_client, post):
    Post.objects.filter(pk=post.pk).update(reaction_counts={"useful": 4})
    with mock.patch.object(services, "schedule_recount"):
        response = member_client.post(_react("post", post), {"kind": "useful"}, headers=HTMX)
    html = response.content.decode()
    assert response.status_code == 200 and "<html" not in html
    assert 'aria-pressed="true"' in html and ">5<" in html
    Post.objects.filter(pk=post.pk).update(reaction_counts={"useful": 5})  # the deferred recount
    with mock.patch.object(services, "schedule_recount"):
        response = member_client.post(_react("post", post), {"kind": "useful"}, headers=HTMX)
    html = response.content.decode()
    assert 'aria-pressed="true"' not in html and ">4<" in html


def test_react_on_comment_redirects_to_the_comment(member_client, member, post, author,
                                                   make_comment):  # fmt: skip
    comment = make_comment(post, author)
    response = member_client.post(_react("comment", comment), {"kind": "thanks"})
    assert response["Location"] == f"{_detail(post)}#comment-{comment.public_id}"
    assert Reaction.objects.filter(user=member, comment=comment, kind="thanks").exists()
    response = member_client.post(_react("comment", comment), {"kind": "thanks"}, headers=HTMX)
    assert response.status_code == 200 and 'aria-pressed="false"' in response.content.decode()


def test_own_content_reaction_refused(client, author, post):
    client.force_login(author)
    response = client.post(_react("post", post), {"kind": "useful"}, follow=True)
    assert "You cannot do this on your own content." in response.content.decode()
    assert not Reaction.objects.exists()
    htmx = client.post(_react("post", post), {"kind": "useful"}, headers=HTMX)
    assert (htmx.status_code, htmx["HX-Redirect"]) == (204, _detail(post))


def test_unknown_reaction_kind_is_refused(member_client, post):
    response = member_client.post(_react("post", post), {"kind": "love"}, follow=True)
    assert response.status_code == 200 and not Reaction.objects.exists()


def test_reaction_buttons_reflect_own_reactions(member_client, member, post, author, make_comment):
    comment = make_comment(post, author)
    Reaction.objects.create(user=member, post=post, kind="insightful")
    Reaction.objects.create(user=member, comment=comment, kind="thanks")
    html = member_client.get(_detail(post)).content.decode()
    assert html.count('aria-pressed="true"') == 2
    assert _react("comment", comment) in html


# --- Bookmarks ------------------------------------------------------------------------------


def test_bookmark_toggle_plain_and_htmx(member_client, member, post):
    response = member_client.post(_bookmark(post))
    assert response["Location"] == _detail(post)
    assert Bookmark.objects.filter(user=member, post=post).exists()
    response = member_client.post(_bookmark(post), headers=HTMX)
    html = response.content.decode()
    assert response.status_code == 200 and 'aria-pressed="false"' in html and "<html" not in html
    assert not Bookmark.objects.exists()
    html = member_client.post(_bookmark(post), headers=HTMX).content.decode()
    assert 'aria-pressed="true"' in html


def test_bookmarks_page_lists_and_filters_by_collection(
    member_client, member, community, author, make_post
):
    reading = BookmarkCollection.objects.create(user=member, name="Reading")
    first = make_post(community, author, title="First saved")
    second = make_post(community, author, title="Second saved")
    Bookmark.objects.create(user=member, post=first, collection=reading)
    Bookmark.objects.create(user=member, post=second)
    response = member_client.get(reverse("posts:bookmarks"))
    html = response.content.decode()
    assert response.status_code == 200
    assert_single_h1(response)
    assert "First saved" in html and "Second saved" in html and "Reading" in html
    html = member_client.get(
        reverse("posts:bookmarks"), {"collection": str(reading.public_id)}
    ).content.decode()
    assert "First saved" in html and "Second saved" not in html
    html = member_client.get(reverse("posts:bookmarks"), {"collection": "none"}).content.decode()
    assert "First saved" not in html and "Second saved" in html


def test_bookmarks_page_of_another_user_collection_is_404(member_client, author):
    theirs = BookmarkCollection.objects.create(user=author, name="Theirs")
    response = member_client.get(reverse("posts:bookmarks"), {"collection": str(theirs.public_id)})
    assert response.status_code == 404


def test_bookmarks_page_empty_state(member_client):
    html = member_client.get(reverse("posts:bookmarks")).content.decode()
    assert "No bookmarks yet" in html


def test_bookmark_no_longer_visible_shows_content_not_accessible(
    client, make_user, make_community, add_member, make_post
):
    private = make_community("Private", access_mode=REQUEST)
    owner = make_user("owner@example.com")
    reader = make_user("reader@example.com")
    add_member(private, owner)
    membership = add_member(private, reader)
    post = make_post(private, owner, title="Secret plans")
    Bookmark.objects.create(user=reader, post=post)
    membership.delete()
    client.force_login(reader)
    html = client.get(reverse("posts:bookmarks")).content.decode()
    assert "Content not accessible" in html and "Secret plans" not in html
    remove = reverse("posts:bookmark_remove", args=[post.public_id])
    assert remove in html
    response = client.post(remove)
    assert response["Location"] == reverse("posts:bookmarks")
    assert not Bookmark.objects.exists()


def test_bookmarks_pagination(member_client, member, community, author, make_post):
    for index in range(21):
        Bookmark.objects.create(user=member, post=make_post(community, author, title=f"P{index}"))
    html = member_client.get(reverse("posts:bookmarks")).content.decode()
    assert 'aria-label="Pagination"' in html
    page2 = member_client.get(reverse("posts:bookmarks"), {"page": 2}).content.decode()
    assert page2.count("tl-bookmark-entry") == 1


def test_bookmarks_page_query_ceiling(
    member_client, member, community, author, make_post, make_community, add_member,
    django_assert_max_num_queries,
):  # fmt: skip
    collection = BookmarkCollection.objects.create(user=member, name="Reading")
    BookmarkCollection.objects.create(user=member, name="Later")
    other = make_community("Data guild")
    for index in range(20):
        target = make_post(other if index % 2 else community, author, title=f"P{index}")
        Bookmark.objects.create(user=member, post=target, collection=collection)
    with django_assert_max_num_queries(12):
        response = member_client.get(reverse("posts:bookmarks"))
    assert response.status_code == 200


def test_collection_create_rename_delete(member_client, member):
    response = member_client.post(reverse("posts:collection_create"), {"name": "Reading"})
    assert response["Location"] == reverse("posts:bookmarks")
    collection = BookmarkCollection.objects.get(user=member)
    rename = reverse("posts:collection_rename", args=[collection.public_id])
    member_client.post(rename, {"name": "Later"})
    collection.refresh_from_db()
    assert collection.name == "Later"
    response = member_client.post(
        reverse("posts:collection_create"), {"name": "later"}, follow=True
    )
    assert "You already have a collection with this name." in response.content.decode()
    delete = reverse("posts:collection_delete", args=[collection.public_id])
    member_client.post(delete)
    assert not BookmarkCollection.objects.exists()


def test_collection_actions_htmx_redirect(member_client):
    response = member_client.post(
        reverse("posts:collection_create"), {"name": "Reading"}, headers=HTMX
    )
    assert (response.status_code, response["HX-Redirect"]) == (204, reverse("posts:bookmarks"))
    response = member_client.post(reverse("posts:collection_create"), {"name": ""}, headers=HTMX)
    assert response.status_code == 204


def test_collection_of_another_user_is_404(member_client, author):
    theirs = BookmarkCollection.objects.create(user=author, name="Theirs")
    for name in ("collection_rename", "collection_delete"):
        url = reverse(f"posts:{name}", args=[theirs.public_id])
        assert member_client.post(url, {"name": "Mine"}).status_code == 404
    assert BookmarkCollection.objects.get().name == "Theirs"


def test_move_bookmark_to_a_collection(member_client, member, post):
    collection = BookmarkCollection.objects.create(user=member, name="Reading")
    bookmark = Bookmark.objects.create(user=member, post=post)
    move = reverse("posts:collection_move", args=[post.public_id])
    response = member_client.post(move, {"collection": str(collection.public_id)})
    assert response["Location"] == reverse("posts:bookmarks")
    bookmark.refresh_from_db()
    assert bookmark.collection == collection
    member_client.post(move, {"collection": ""})
    bookmark.refresh_from_db()
    assert bookmark.collection is None


def test_move_to_another_user_collection_is_404(member_client, member, author, post):
    theirs = BookmarkCollection.objects.create(user=author, name="Theirs")
    Bookmark.objects.create(user=member, post=post)
    move = reverse("posts:collection_move", args=[post.public_id])
    assert member_client.post(move, {"collection": str(theirs.public_id)}).status_code == 404


def test_bookmarks_navigation_entry(member_client):
    html = member_client.get(reverse("posts:bookmarks")).content.decode()
    assert f'href="{reverse("posts:bookmarks")}"' in html


# --- Reports --------------------------------------------------------------------------------


def test_report_form_page(member_client, post):
    response = member_client.get(_report("post", post))
    html = response.content.decode()
    assert response.status_code == 200
    assert_single_h1(response)
    assert 'type="radio"' in html and "Confidential information" in html
    assert 'name="details"' in html and "Hello" in html


def test_report_plain_redirects_with_thanks(member_client, member, post):
    response = member_client.post(
        _report("post", post), {"reason": "outdated", "details": "Old version"}, follow=True
    )
    assert "Thank you, moderators have been informed" in response.content.decode()
    assert response.redirect_chain[-1][0] == _detail(post)
    report = ContentReport.objects.get()
    assert (report.reporter, report.reason, report.details) == (member, "outdated", "Old version")


def test_report_htmx_returns_the_confirmation(member_client, post):
    response = member_client.post(_report("post", post), {"reason": "spam"}, headers=HTMX)
    html = response.content.decode()
    assert response.status_code == 200 and "<html" not in html
    assert "Thank you, moderators have been informed" in html


def test_report_comment_redirects_to_the_comment(member_client, post, author, make_comment):
    comment = make_comment(post, author)
    response = member_client.post(_report("comment", comment), {"reason": "spam"})
    assert response["Location"] == f"{_detail(post)}#comment-{comment.public_id}"


def test_duplicate_report_is_an_error_toast(member_client, post):
    member_client.post(_report("post", post), {"reason": "spam"})
    response = member_client.post(_report("post", post), {"reason": "other"}, follow=True)
    assert "You have already reported this content." in response.content.decode()
    assert ContentReport.objects.count() == 1


def test_report_without_reason_redisplays_the_form(member_client, post):
    response = member_client.post(_report("post", post), {"details": "x"})
    assert response.status_code == 200
    assert "This field is required." in response.content.decode()
    assert not ContentReport.objects.exists()


def test_own_content_report_refused(client, author, post):
    client.force_login(author)
    response = client.post(_report("post", post), {"reason": "spam"}, follow=True)
    assert "You cannot do this on your own content." in response.content.decode()


# --- Detail page ----------------------------------------------------------------------------


def test_detail_query_ceiling_with_interactions(
    member_client, member, post, author, make_comment, django_assert_max_num_queries
):
    for _ in range(10):
        top = make_comment(post, author, reaction_counts={"useful": 1})
        make_comment(post, member, parent=top)
        Reaction.objects.create(user=member, comment=top, kind="useful")
    Bookmark.objects.create(user=member, post=post)
    with django_assert_max_num_queries(20):
        response = member_client.get(_detail(post))
    assert response.status_code == 200


def test_interactions_in_french(client, member, post):
    member.profile.language = "fr"
    member.profile.save()
    client.force_login(member)
    html = client.get(_detail(post), headers={"accept-language": "fr"}).content.decode()
    assert "Publier le commentaire" in html and "Signaler" in html and "Utile" in html
    response = client.post(_report("post", post), {"reason": "spam"}, follow=True)
    assert "Merci, les modérateurs ont été informés" in response.content.decode()
