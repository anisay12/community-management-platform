"""HTTP tests of the read side of posts: community feed, home feed, post detail, revisions."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone

from accounts.roles import Role
from communities.models import AdminAccessGrant, Community
from communities.roles import CommunityRole
from core.tests.helpers import assert_single_h1
from posts.models import Comment, Post, PostRevision
from posts.selectors import FEED_PAGE_SIZE

pytestmark = pytest.mark.django_db

OPEN, REQUEST, INVITE = Community.AccessMode
S = Post.Status
K = Post.Kind


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
def member_client(client, member):
    client.force_login(member)
    return client


def _feed(community):
    return reverse("posts:feed", args=[community.slug])


def _detail(post):
    return reverse("posts:detail", args=[post.community.slug, post.public_id])


def _at(minutes):
    return timezone.now() - timedelta(minutes=minutes)


def _staff(make_user, email, role):
    user = make_user(email)
    user.groups.add(Group.objects.get(name=role))
    return user


# --- Access ---------------------------------------------------------------------------------


def test_read_pages_require_login(client, community, author, make_post):
    post = make_post(community, author)
    for url in (_feed(community), _detail(post), reverse("posts:home_feed")):
        assert client.get(url).status_code == 302


@pytest.mark.parametrize("mode", [REQUEST, INVITE])
def test_non_member_gets_404_on_private_post_and_feed(
    client, make_user, make_community, add_member, make_post, mode
):
    private = make_community("Private", access_mode=mode, listed=True)
    author = make_user("author@example.com")
    add_member(private, author)
    post = make_post(private, author)
    client.force_login(make_user("outsider@example.com"))
    assert client.get(_detail(post)).status_code == 404
    assert client.get(_feed(private)).status_code == 404
    assert (
        client.get(reverse("posts:revisions", args=[private.slug, post.public_id])).status_code
        == 404
    )


def test_open_feed_readable_by_any_employee(client, make_user, community, author, make_post):
    make_post(community, author, title="Hello world")
    client.force_login(make_user("outsider@example.com"))
    response = client.get(_feed(community))
    assert response.status_code == 200
    assert_single_h1(response)
    assert "Hello world" in response.content.decode()


@pytest.mark.parametrize("role", [Role.TECHNICAL_ADMIN, Role.AUDITOR])
def test_open_feed_forbidden_to_technical_admin_and_auditor(
    client, make_user, verified_login, community, author, make_post, role
):
    post = make_post(community, author)
    verified_login(client, _staff(make_user, "staff@example.com", role))
    assert client.get(_feed(community)).status_code == 403
    assert client.get(_detail(post)).status_code == 404


def test_functional_admin_without_grant_gets_403_then_reads_with_grant(
    client, functional_admin, verified_login, make_community, make_user, add_member, make_post
):
    private = make_community("Private", access_mode=REQUEST)
    author = make_user("author@example.com")
    add_member(private, author)
    post = make_post(private, author, title="Confidential")
    verified_login(client, functional_admin)
    assert client.get(_feed(private)).status_code == 403
    assert client.get(_detail(post)).status_code == 404
    AdminAccessGrant.objects.create(community=private, user=functional_admin, reason="Check")
    assert "Confidential" in client.get(_feed(private)).content.decode()
    assert client.get(_detail(post)).status_code == 200


def test_wrong_community_slug_is_404(member_client, community, author, make_community, make_post):
    post = make_post(community, author)
    make_community("Elsewhere")
    url = reverse("posts:detail", args=["elsewhere", post.public_id])
    assert member_client.get(url).status_code == 404
    assert member_client.get(reverse("posts:feed", args=["nope"])).status_code == 404


def test_draft_is_404_for_others_and_never_in_feeds(
    member_client, client, community, author, make_post
):
    draft = make_post(community, author, title="Secret draft", status=S.DRAFT)
    assert member_client.get(_detail(draft)).status_code == 404
    assert "Secret draft" not in member_client.get(_feed(community)).content.decode()
    client.force_login(author)
    assert client.get(_detail(draft)).status_code == 200
    assert "Secret draft" not in client.get(_feed(community)).content.decode()
    assert "Secret draft" not in client.get(reverse("posts:home_feed")).content.decode()


# --- Community feed -------------------------------------------------------------------------


def test_feed_is_a_community_tab_with_cards(
    member_client, community, author, make_post, make_community
):
    other = make_community("Data guild")
    original = make_post(other, author, title="Original")
    post = make_post(
        community,
        author,
        title="Hello",
        kind=K.QUESTION,
        comment_count=3,
        reaction_counts={"useful": 2, "thanks": 0},
        shared_from=original,
    )
    from taxonomy.models import Tag

    post.tags.add(Tag.objects.create(name="Django", key="django"))
    response = member_client.get(_feed(community))
    html = response.content.decode()
    assert_single_h1(response)
    assert 'aria-current="page">Feed</a>' in html
    assert html.index(">Feed</a>") < html.index(">About</a>")
    assert "Hello" in html and _detail(post) in html
    assert "Ann Author" in html
    assert "Question" in html
    assert "Django" in html
    assert "3 comments" in html
    assert "Useful" in html and "Thanks" not in html
    assert "<time datetime=" in html
    assert "Also published in" in html and "Data guild" in html
    assert _detail(original) in html


def test_share_of_inaccessible_original(
    member_client, community, author, make_community, make_post
):
    secret = make_community("Secret", access_mode=INVITE)
    original = make_post(secret, author, title="Classified")
    make_post(community, author, title="Shared", shared_from=original)
    html = member_client.get(_feed(community)).content.decode()
    assert "Content not accessible" in html
    assert "Secret" not in html and "Classified" not in html


def test_feed_pinned_badge_and_filters(member_client, community, author, make_post):
    make_post(community, author, title="Pinned one", pinned_at=_at(1), last_activity_at=_at(90))
    make_post(community, author, title="Recent talk", kind=K.DISCUSSION)
    make_post(community, author, title="A question", kind=K.QUESTION, last_activity_at=_at(5))
    html = member_client.get(_feed(community)).content.decode()
    assert html.index("Pinned one") < html.index("Recent talk")
    assert "Pinned</span>" in html
    assert 'href="?kind=question"' in html
    filtered = member_client.get(_feed(community), {"kind": "question"})
    content = filtered.content.decode()
    assert "A question" in content and "Recent talk" not in content
    assert 'href="?kind=question" aria-current="page"' in content
    unanswered = member_client.get(_feed(community), {"unanswered": "1"}).content.decode()
    assert "A question" in unanswered and "Recent talk" not in unanswered
    assert 'href="?unanswered=1" aria-current="page"' in unanswered


@pytest.mark.parametrize(
    ("query", "title"),
    [
        ({}, "No posts yet"),
        ({"kind": "question"}, "No questions yet"),
        ({"kind": "announcement"}, "No announcements yet"),
        ({"unanswered": "1"}, "No unanswered questions"),
    ],
)
def test_feed_empty_states(member_client, community, query, title):
    html = member_client.get(_feed(community), query).content.decode()
    assert "tl-empty-state" in html and title in html


def test_feed_load_more_with_htmx_and_fallback(member_client, community, author, make_post):
    for index in range(FEED_PAGE_SIZE + 2):
        make_post(community, author, title=f"Post {index:02d}", last_activity_at=_at(index))
    html = member_client.get(_feed(community)).content.decode()
    assert "Post 19" in html and "Post 20" not in html
    assert "hx-get=" in html and "?cursor=" in html
    response = member_client.get(_feed(community))
    cursor = response.context["page"].next_cursor
    more = member_client.get(_feed(community), {"cursor": cursor}, headers={"HX-Request": "true"})
    fragment = more.content.decode()
    assert "Post 20" in fragment and "Post 21" in fragment and "Post 00" not in fragment
    assert "<h1" not in fragment and "<html" not in fragment
    full = member_client.get(_feed(community), {"cursor": cursor}).content.decode()
    assert "Post 20" in full and "<h1" in full


def test_feed_query_count(
    member_client, community, author, make_post, make_community, django_assert_max_num_queries
):
    from taxonomy.models import Tag

    tag = Tag.objects.create(name="Django", key="django")
    original = make_post(make_community("Other"), author)
    for index in range(FEED_PAGE_SIZE):
        post = make_post(
            community,
            author,
            title=f"Post {index}",
            shared_from=original if index % 2 else None,
            pinned_at=_at(index) if index < 3 else None,
        )
        post.tags.add(tag)
    with django_assert_max_num_queries(15):
        response = member_client.get(_feed(community))
    assert response.status_code == 200


def test_pending_and_hidden_badges_for_author(client, community, author, make_post):
    make_post(community, author, title="Waiting", status=S.PENDING_REVIEW)
    make_post(community, author, title="Gone", status=S.HIDDEN, hidden_reason="Off topic")
    client.force_login(author)
    html = client.get(_feed(community)).content.decode()
    assert "Pending review</span>" in html
    assert "Hidden</span>" in html


# --- Home feed ------------------------------------------------------------------------------


def test_home_feed(member_client, member, community, author, make_post, make_community):
    make_post(community, author, title="From my guild")
    make_post(make_community("Not joined"), author, title="Elsewhere")
    response = member_client.get(reverse("posts:home_feed"))
    html = response.content.decode()
    assert response.status_code == 200
    assert_single_h1(response)
    assert "From my guild" in html and "Elsewhere" not in html
    assert "Python guild" in html
    assert 'data-nav-key="home_feed"' in html


def test_home_feed_empty_state_points_to_catalogue(client, make_user):
    client.force_login(make_user("lonely@example.com"))
    html = client.get(reverse("posts:home_feed")).content.decode()
    assert "tl-empty-state" in html
    assert f'href="{reverse("communities:catalogue")}"' in html


def test_home_feed_load_more(member_client, community, author, make_post):
    for index in range(FEED_PAGE_SIZE + 1):
        make_post(community, author, title=f"Post {index:02d}", last_activity_at=_at(index))
    response = member_client.get(reverse("posts:home_feed"))
    cursor = response.context["page"].next_cursor
    more = member_client.get(
        reverse("posts:home_feed"), {"cursor": cursor}, headers={"HX-Request": "true"}
    )
    assert "Post 20" in more.content.decode() and "<h1" not in more.content.decode()


def test_home_feed_query_count(
    member_client, member, make_community, add_member, author, make_post,
    django_assert_max_num_queries,
):  # fmt: skip
    for index in range(FEED_PAGE_SIZE):
        community = make_community(f"Guild {index}")
        add_member(community, member)
        make_post(community, author, title=f"Post {index}")
    with django_assert_max_num_queries(15):
        response = member_client.get(reverse("posts:home_feed"))
    assert len(response.context["page"].items) == FEED_PAGE_SIZE


# --- Detail ---------------------------------------------------------------------------------


def test_detail_page(member_client, community, author, make_post, make_comment, make_user,
                     add_member):  # fmt: skip
    expert = make_user("expert@example.com", first_name="Eli", last_name="Expert")
    add_member(community, expert, CommunityRole.EXPERT)
    post = make_post(community, author, title="How to test?", kind=K.QUESTION,
                     body="Use **pytest**")  # fmt: skip
    first = make_comment(post, expert, body="Plain answer")
    answer = make_comment(post, expert, body="The accepted one", is_expert_answer=True)
    make_comment(post, author, parent=first, body="Thanks for the reply")
    Post.objects.filter(pk=post.pk).update(accepted_answer=answer)
    response = member_client.get(_detail(post))
    html = response.content.decode()
    assert response.status_code == 200
    assert_single_h1(response)
    assert "<strong>pytest</strong>" in html
    assert 'aria-label="Breadcrumb"' in html
    assert reverse("communities:catalogue") in html and _feed(community) in html
    assert "Accepted answer" in html and "Expert answer" in html
    assert html.index("The accepted one") < html.index("Plain answer")
    assert "Thanks for the reply" in html
    assert reverse("posts:revisions", args=[community.slug, post.public_id]) not in html


def test_detail_hidden_comments(client, community, author, member, make_post, make_comment,
                                make_user, add_member):  # fmt: skip
    post = make_post(community, author)
    make_comment(post, member, body="Something rude", status=Comment.Status.HIDDEN,
                 hidden_reason="Insult")  # fmt: skip
    reader = make_user("reader@example.com")
    add_member(community, reader)
    moderator = make_user("mod@example.com")
    add_member(community, moderator, CommunityRole.MODERATOR)
    client.force_login(reader)
    html = client.get(_detail(post)).content.decode()
    assert "This comment is hidden." in html and "Something rude" not in html
    for viewer in (member, moderator):
        client.force_login(viewer)
        html = client.get(_detail(post)).content.decode()
        assert "Something rude" in html and "Insult" in html


def test_detail_of_hidden_post_for_author_shows_reason(client, community, author, make_post):
    post = make_post(community, author, status=S.HIDDEN, hidden_reason="Duplicate topic")
    client.force_login(author)
    html = client.get(_detail(post)).content.decode()
    assert "Duplicate topic" in html and "Hidden</span>" in html


def test_automatic_hiding_shows_a_translated_reason(client, community, author, member, make_post,
                                                    make_comment):  # fmt: skip
    post = make_post(community, author, status=S.HIDDEN, hidden_reason="automatic")
    make_comment(post, author, body="Mine", status=Comment.Status.HIDDEN,
                 hidden_reason="automatic")  # fmt: skip
    client.force_login(author)
    html = client.get(_detail(post)).content.decode()
    assert "Hidden automatically after several reports" in html
    assert "Reason for hiding: automatic" not in html
    assert "This post is hidden." in html
    client.force_login(author)
    html = client.get(_detail(post), headers={"Accept-Language": "fr"}).content.decode()
    assert "Masqué automatiquement après plusieurs signalements" in html


def test_hidden_accepted_answer_is_not_highlighted(member_client, community, author, member,
                                                   make_post, make_comment):  # fmt: skip
    post = make_post(community, author, kind=K.QUESTION)
    answer = make_comment(post, author, body="Secret answer", status=Comment.Status.HIDDEN,
                          hidden_reason="Leak")  # fmt: skip
    Post.objects.filter(pk=post.pk).update(accepted_answer=answer)
    html = member_client.get(_detail(post)).content.decode()
    assert 'id="accepted-answer-title"' not in html
    assert "Secret answer" not in html and "This comment is hidden." in html


def test_detail_checks_moderator_rights_once(member_client, community, author, make_post,
                                            make_comment, monkeypatch):  # fmt: skip
    from posts import policies

    post = make_post(community, author)
    make_comment(post, author)
    calls = []
    original = policies.is_content_moderator

    def counting(user, community):
        calls.append(user)
        return original(user, community)

    monkeypatch.setattr(policies, "is_content_moderator", counting)
    assert member_client.get(_detail(post)).status_code == 200
    assert len(calls) == 1


def test_feeds_vary_on_htmx_requests(member_client, community):
    for url in (_feed(community), reverse("posts:home_feed")):
        response = member_client.get(url)
        assert "HX-Request" in response["Vary"]


def test_tampered_cursor_falls_back_to_the_first_page(member_client, community, author,
                                                     make_post):  # fmt: skip
    import base64

    make_post(community, author, title="Only post")
    cursor = base64.urlsafe_b64encode(b"0001-01-01T00:00:00+20:00|5").decode()
    response = member_client.get(_feed(community), {"cursor": cursor})
    assert response.status_code == 200 and "Only post" in response.content.decode()


def test_detail_query_count(member_client, community, author, make_post, make_comment,
                            django_assert_max_num_queries):  # fmt: skip
    post = make_post(community, author, kind=K.QUESTION)
    tops = [make_comment(post, author) for _ in range(10)]
    for top in tops:
        make_comment(post, author, parent=top)
        make_comment(post, author, parent=top, status=Comment.Status.HIDDEN)
    Post.objects.filter(pk=post.pk).update(accepted_answer=tops[0])
    with django_assert_max_num_queries(20):
        response = member_client.get(_detail(post))
    assert response.status_code == 200


def test_detail_and_feed_in_french(client, member, community, author, make_post, make_comment):
    member.profile.language = "fr"
    member.profile.save()
    post = make_post(community, author, kind=K.QUESTION, comment_count=2)
    make_comment(post, author, status=Comment.Status.HIDDEN)
    client.force_login(member)
    feed = client.get(_feed(community), headers={"accept-language": "fr"}).content.decode()
    assert '<html lang="fr"' in feed
    assert "Questions sans réponse" in feed and "2 commentaires" in feed and "il y a" in feed
    detail = client.get(_detail(post), headers={"accept-language": "fr"}).content.decode()
    assert "Commentaires" in detail and "Ce commentaire est masqué." in detail
    home = client.get(reverse("posts:home_feed"), headers={"accept-language": "fr"})
    assert "Fil" in home.content.decode()
    empty = client.get(_feed(community), {"kind": "article"}, headers={"accept-language": "fr"})
    assert "Aucun article pour l\u2019instant" in empty.content.decode()


# --- Revisions ------------------------------------------------------------------------------


def test_revisions_for_moderators_only(client, community, author, member, make_post, make_user,
                                       add_member):  # fmt: skip
    post = make_post(community, author, kind=K.ARTICLE, title="Current title")
    PostRevision.objects.create(post=post, editor=author, title="First title", body="Old *body*")
    url = reverse("posts:revisions", args=[community.slug, post.public_id])
    client.force_login(member)
    assert client.get(url).status_code == 403
    moderator = make_user("mod@example.com")
    add_member(community, moderator, CommunityRole.MODERATOR)
    client.force_login(moderator)
    response = client.get(url)
    html = response.content.decode()
    assert response.status_code == 200
    assert_single_h1(response)
    assert "First title" in html and "<em>body</em>" in html and "Ann Author" in html
    assert url in client.get(_detail(post)).content.decode()
