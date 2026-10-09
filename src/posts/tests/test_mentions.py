import pytest

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
    ],
)
def test_handle_for(first, last, handle):
    assert handle_for(User(first_name=first, last_name=last)) == handle


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


def test_render_body_wraps_mentions_outside_code_and_links():
    html = render_body("Hi @alice.doe, see `@code.sample` and [@link.text](https://x.example)")
    assert '<span class="mention">@alice.doe</span>' in html
    assert "<code>@code.sample</code>" in html
    assert '<span class="mention">@link.text' not in html
    assert render_body("") == ""


def test_render_body_stays_sanitised():
    html = render_body("<script>alert(1)</script> @a.b")
    assert "<script>" not in html
    assert '<span class="mention">@a.b</span>' in html
