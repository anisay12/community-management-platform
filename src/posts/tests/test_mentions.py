import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from posts.mentions import extract_handles, handle_for, resolve_mentions, sync_mentions
from posts.models import Mention
from posts.rendering import render_body

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("first", "last", "handle"),
    [
        ("Alice", "Doe", "alice.doe"),
        ("Jean-Rémi", "Lefèvre", "jean-remi.lefevre"),
        ("ÉLODIE", "De La Tour", "elodie.de-la-tour"),
        ("Jean_Paul", "O'Neil", "jean-paul.oneil"),
    ],
)
def test_handle_for(first, last, handle):
    assert handle_for(User(first_name=first, last_name=last)) == handle


def test_handles_always_match_the_mention_pattern():
    handle = handle_for(User(first_name="Jean_Paul", last_name="Søren_Æby"))
    assert extract_handles(f"Hi @{handle}!") == {handle}


def test_extract_handles():
    text = "Hi @Alice.Doe and @jean-remi.lefevre, mail bob@example.com or @nobody."
    assert extract_handles(text) == {"alice.doe", "jean-remi.lefevre"}
    assert extract_handles("") == set()


@pytest.fixture
def community(make_community):
    return make_community()


def test_resolve_mentions(community, make_user, add_member):
    alice = make_user("alice@example.com", first_name="Alicé", last_name="Doe")
    add_member(community, alice)
    outsider = make_user("out@example.com", first_name="Out", last_name="Sider")
    twin1 = make_user("t1@example.com", first_name="Sam", last_name="Twin")
    twin2 = make_user("t2@example.com", first_name="Sam", last_name="Twin")
    add_member(community, twin1)
    add_member(community, twin2)
    text = "@alice.doe @out.sider @sam.twin @nobody.here"
    assert resolve_mentions(community, text) == [alice]
    assert outsider not in resolve_mentions(community, text)
    assert resolve_mentions(community, "no mention") == []


def test_sync_mentions_returns_new_users_only(community, make_user, make_post):
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com", first_name="Bob")
    post = make_post(community, alice)
    assert sync_mentions(post, [alice]) == [alice]
    assert sync_mentions(post, [alice, bob]) == [bob]
    assert sync_mentions(post, [bob]) == []
    assert list(Mention.objects.values_list("mentioned_user", flat=True)) == [bob.pk]


def test_sync_mentions_on_a_comment(community, make_user, make_post, make_comment):
    alice = make_user("alice@example.com")
    comment = make_comment(make_post(community, alice), alice)
    assert sync_mentions(comment, [alice]) == [alice]
    assert Mention.objects.get().comment == comment


def test_render_body_wraps_resolved_mentions_outside_code_and_links():
    resolved = {"alice.doe", "code.sample", "link.text"}
    html = render_body(
        "Hi @alice.doe, see `@code.sample` and [@link.text](https://x.example)",
        resolved_handles=resolved,
    )
    assert '<span class="mention">@alice.doe</span>' in html
    assert "<code>@code.sample</code>" in html
    assert '<span class="mention">@link.text' not in html
    assert render_body("", resolved_handles=resolved) == ""


def test_render_body_leaves_unresolved_mentions_as_plain_text():
    html = render_body("@Alice.Doe and @nobody.here", resolved_handles={"alice.doe"})
    assert '<span class="mention">@Alice.Doe</span>' in html
    assert "@nobody.here" in html and '<span class="mention">@nobody.here' not in html
    assert '<span class="mention">' not in render_body("@alice.doe")


def test_render_body_stays_sanitised():
    html = render_body("<script>alert(1)</script> @a.b", resolved_handles={"a.b"})
    assert "<script>" not in html
    assert '<span class="mention">@a.b</span>' in html


def test_resolve_mentions_matches_accents_and_punctuation(community, make_user, add_member):
    users = [
        make_user("a@example.com", first_name="Jean Rémi", last_name="Lefèvre"),
        make_user("b@example.com", first_name="Jean_Paul", last_name="O'Neil"),
        make_user("c@example.com", first_name="Søren", last_name="Ĳssel"),
    ]
    for user in users:
        add_member(community, user)
    text = " ".join(f"@{handle_for(user)}" for user in users)
    assert set(resolve_mentions(community, text)) == set(users)


def test_resolve_mentions_loads_only_candidates(community, make_user, add_member):
    alice = make_user("alice@example.com", first_name="Alice", last_name="Doe")
    add_member(community, alice)

    def count_queries():
        with CaptureQueriesContext(connection) as context:
            assert resolve_mentions(community, "@alice.doe") == [alice]
        return context

    baseline = len(count_queries())
    for index in range(20):
        member = make_user(f"m{index}@example.com", first_name="M", last_name=str(index))
        add_member(community, member)
    context = count_queries()
    assert len(context) == baseline
    assert any("translate(" in query["sql"] for query in context.captured_queries)
