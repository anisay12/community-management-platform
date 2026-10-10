from datetime import timedelta

import pytest
from django.contrib.postgres.search import SearchQuery
from django.core.cache import cache
from django.utils import timezone

from audit.models import AuditEvent
from communities.models import Community, CommunityMembership
from core.errors import DomainError
from notifications.models import Notification
from posts import services_posts as services
from posts.models import Comment, Mention, Post, PostRevision
from taxonomy.models import Tag
from taxonomy.services import get_or_create_tag

pytestmark = pytest.mark.django_db

Role = CommunityMembership.Role


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def broadcasts(monkeypatch):
    """Calls to ``broadcast_post.delay`` as ``(post_id, category)`` tuples."""
    calls = []
    monkeypatch.setattr(
        "posts.tasks.broadcast_post.delay",
        lambda post_id, category: calls.append((post_id, category)),
    )
    return calls


@pytest.fixture
def community(make_community):
    return make_community()


@pytest.fixture
def people(make_user, community, add_member):
    """One user per community role, all members of ``community``."""
    found = {}
    for role in Role:
        user = make_user(f"{role}@example.com", first_name=role.capitalize(), last_name="Person")
        add_member(community, user, role)
        found[str(role)] = user
    return found


@pytest.fixture
def member(people):
    return people["member"]


@pytest.fixture
def moderator(people):
    return people["moderator"]


@pytest.fixture
def outsider(make_user):
    return make_user("outsider@example.com", first_name="Out", last_name="Sider")


def _code(callable_, **kwargs):
    with pytest.raises(DomainError) as error:
        callable_(**kwargs)
    return error.value.code


# Creation ------------------------------------------------------------------------------


def test_member_publishes_a_discussion(
    member, community, broadcasts, django_capture_on_commit_callbacks
):
    before = timezone.now()
    with django_capture_on_commit_callbacks(execute=True):
        post = services.create_post(
            actor=member,
            community=community,
            kind=Post.Kind.DISCUSSION,
            title="  Hello  ",
            body="Some **bold** text",
        )
    post.refresh_from_db()
    assert post.status == Post.Status.PUBLISHED
    assert post.title == "Hello"
    assert post.author == member
    assert post.author_display == member.get_full_name()
    assert "<strong>bold</strong>" in post.body_html
    assert post.published_at >= before and post.last_activity_at >= before
    community.refresh_from_db()
    assert community.last_activity_at >= before
    assert broadcasts == [(post.pk, "community_post")]
    assert Post.objects.filter(
        pk=post.pk, search_vector=SearchQuery("bold", config="simple")
    ).exists()


def test_announcement_broadcasts_in_the_announcement_category(
    people, community, broadcasts, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        post = services.create_post(
            actor=people["animator"],
            community=community,
            kind=Post.Kind.ANNOUNCEMENT,
            title="News",
            body="Body",
        )
    assert broadcasts == [(post.pk, "announcement")]


def test_draft_is_not_broadcast(member, community, broadcasts, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        post = services.create_post(
            actor=member,
            community=community,
            kind=Post.Kind.QUESTION,
            title="Draft",
            body="Later",
            publish=False,
        )
    assert post.status == Post.Status.DRAFT
    assert post.published_at is None
    assert broadcasts == []


@pytest.mark.parametrize(
    ("role", "kind", "allowed"),
    [
        ("member", Post.Kind.DISCUSSION, True),
        ("member", Post.Kind.QUESTION, True),
        ("member", Post.Kind.ARTICLE, False),
        ("member", Post.Kind.ANNOUNCEMENT, False),
        ("contributor", Post.Kind.ARTICLE, True),
        ("contributor", Post.Kind.ANNOUNCEMENT, False),
        ("moderator", Post.Kind.ANNOUNCEMENT, False),
        ("animator", Post.Kind.ANNOUNCEMENT, True),
        ("owner", Post.Kind.ANNOUNCEMENT, True),
    ],
)
def test_kind_rules(people, community, broadcasts, role, kind, allowed):
    kwargs = {
        "actor": people[role],
        "community": community,
        "kind": kind,
        "title": "T",
        "body": "B",
    }
    if allowed:
        assert services.create_post(**kwargs).kind == kind
    else:
        assert _code(services.create_post, **kwargs) == "kind_forbidden"


def test_functional_admin_may_announce_in_an_open_community(
    functional_admin, community, broadcasts
):
    post = services.create_post(
        actor=functional_admin,
        community=community,
        kind=Post.Kind.ANNOUNCEMENT,
        title="Admin news",
        body="B",
    )
    assert post.status == Post.Status.PUBLISHED


def test_non_member_and_read_only_community(outsider, member, community):
    kwargs = {"community": community, "kind": Post.Kind.DISCUSSION, "title": "T", "body": "B"}
    assert _code(services.create_post, actor=outsider, **kwargs) == "not_member"
    for status in (Community.Status.SUSPENDED, Community.Status.ARCHIVED):
        Community.objects.filter(pk=community.pk).update(status=status)
        community.refresh_from_db()
        assert _code(services.create_post, actor=member, **kwargs) == "read_only"


def test_author_display_never_shows_an_email(make_user, community, add_member, broadcasts):
    nameless = make_user("j.smith@example.com", first_name="", last_name="")
    add_member(community, nameless)
    post = services.create_post(
        actor=nameless, community=community, kind=Post.Kind.DISCUSSION, title="T", body="B"
    )
    assert post.author_display == "j.smith"


def test_invalid_content_is_refused(member, community):
    kwargs = {"actor": member, "community": community, "kind": Post.Kind.DISCUSSION}
    assert _code(services.create_post, title="  ", body="B", **kwargs) == "title_required"
    assert _code(services.create_post, title="T", body="x" * 50_001, **kwargs) == "body_too_long"
    assert _code(services.create_post, title="T", body="B", **{**kwargs, "kind": "poll"}) == (
        "kind_forbidden"
    )


def test_post_rate_limit(member, community, settings, broadcasts):
    settings.POSTS_RATE_LIMITS = {**settings.POSTS_RATE_LIMITS, "post": 2}
    kwargs = {"actor": member, "community": community, "kind": Post.Kind.DISCUSSION, "body": "B"}
    services.create_post(title="One", **kwargs)
    services.create_post(title="Two", **kwargs)
    with pytest.raises(DomainError) as error:
        services.create_post(title="Three", **kwargs)
    assert error.value.code == "rate_limited"
    assert error.value.retry_after > 0


def test_mentions_are_stored_and_notified_on_publish(
    member, people, community, broadcasts, django_capture_on_commit_callbacks
):
    expert = people["expert"]
    with django_capture_on_commit_callbacks(execute=True):
        post = services.create_post(
            actor=member,
            community=community,
            kind=Post.Kind.DISCUSSION,
            title="T",
            body="Ping @expert.person and @nobody.here",
        )
    assert list(Mention.objects.filter(post=post).values_list("mentioned_user", flat=True)) == [
        expert.pk
    ]
    assert Notification.objects.filter(recipient=expert, category="mention").count() == 1
    assert '<span class="mention">@expert.person</span>' in post.body_html
    assert '<span class="mention">@nobody.here' not in post.body_html


def test_mentions_in_a_draft_are_notified_only_when_published(
    member, people, community, broadcasts, django_capture_on_commit_callbacks
):
    expert = people["expert"]
    with django_capture_on_commit_callbacks(execute=True):
        post = services.create_post(
            actor=member,
            community=community,
            kind=Post.Kind.DISCUSSION,
            title="T",
            body="Ping @expert.person",
            publish=False,
        )
    assert not Notification.objects.filter(category="mention").exists()
    with django_capture_on_commit_callbacks(execute=True):
        services.publish_draft(actor=member, post=post)
    assert Notification.objects.filter(recipient=expert, category="mention").count() == 1
    assert broadcasts == [(post.pk, "community_post")]


def test_mentions_are_resolved_again_at_publication(
    member, people, community, make_user, add_member, broadcasts,
    django_capture_on_commit_callbacks,
):  # fmt: skip
    expert = people["expert"]
    post = services.create_post(
        actor=member, community=community, kind=Post.Kind.DISCUSSION, title="T",
        body="Ping @expert.person and @new.comer", publish=False,
    )  # fmt: skip
    CommunityMembership.objects.filter(community=community, user=expert).delete()
    newcomer = make_user("new@example.com", first_name="New", last_name="Comer")
    add_member(community, newcomer)
    Community.objects.filter(pk=community.pk).update(access_mode=Community.AccessMode.INVITE)
    post.community.refresh_from_db()
    with django_capture_on_commit_callbacks(execute=True):
        services.publish_draft(actor=member, post=post)
    mentions = Notification.objects.filter(category="mention")
    mentioned = set(mentions.values_list("recipient", flat=True))
    assert mentioned == {newcomer.pk}
    assert list(Mention.objects.filter(post=post).values_list("mentioned_user", flat=True)) == [
        newcomer.pk
    ]
    post.refresh_from_db()
    assert '<span class="mention">@new.comer</span>' in post.body_html
    assert '<span class="mention">@expert.person' not in post.body_html


def test_mention_notifications_on_approval_come_from_the_author(
    member, people, reviewed, broadcasts, django_capture_on_commit_callbacks
):
    expert = people["expert"]
    post = services.create_post(
        actor=member, community=reviewed, kind=Post.Kind.DISCUSSION, title="T",
        body="Ping @expert.person",
    )  # fmt: skip
    with django_capture_on_commit_callbacks(execute=True):
        services.approve_review(actor=people["moderator"], post=post)
    notification = Notification.objects.get(category="mention", recipient=expert)
    assert notification.actor == member


# Tags ----------------------------------------------------------------------------------


def test_contributor_creates_tags_member_only_picks(people, member, community, broadcasts):
    Tag.objects.create(name="Django", slug="django")
    post = services.create_post(
        actor=people["contributor"],
        community=community,
        kind=Post.Kind.DISCUSSION,
        title="T",
        body="B",
        tags=["django", "Élan", " élan ", ""],
    )
    assert sorted(post.tags.values_list("name", flat=True)) == ["Django", "Élan"]
    assert Post.objects.filter(
        pk=post.pk, search_vector=SearchQuery("django", config="simple")
    ).exists()

    kwargs = {"actor": member, "community": community, "kind": Post.Kind.DISCUSSION}
    assert services.create_post(title="T", body="B", tags=["DJANGO"], **kwargs).tags.count() == 1
    assert _code(services.create_post, title="T", body="B", tags=["brand-new"], **kwargs) == (
        "tag_creation_forbidden"
    )
    assert not Tag.objects.filter(name="brand-new").exists()


def test_set_tags_bumps_and_checks_the_version(member, community, make_post):
    for name in ("Python", "Go"):
        Tag.objects.create(name=name, slug=name.lower())
    post = make_post(community, member)
    services.set_tags(actor=member, post=post, names=["python"])
    post.refresh_from_db()
    assert post.version == 2
    stale = {"actor": member, "post": post, "names": ["go"], "version": 1}
    assert _code(services.set_tags, **stale) == "edit_conflict"
    services.set_tags(actor=member, post=post, names=["go"], version=2)
    post.refresh_from_db()
    assert post.version == 3 and [t.name for t in post.tags.all()] == ["Go"]


def test_set_tags(member, people, community, make_post):
    post = make_post(community, member)
    assert _code(services.set_tags, actor=member, post=post, names=["Python"]) == (
        "tag_creation_forbidden"
    )
    services.set_tags(actor=people["moderator"], post=post, names=["Python", "Rust"])
    assert sorted(post.tags.values_list("name", flat=True)) == ["Python", "Rust"]
    services.set_tags(actor=member, post=post, names=["rust"])
    assert list(post.tags.values_list("name", flat=True)) == ["Rust"]
    assert Post.objects.filter(
        pk=post.pk, search_vector=SearchQuery("rust", config="simple")
    ).exists()
    other = people["contributor"]
    assert _code(services.set_tags, actor=other, post=post, names=["rust"]) == "forbidden"


# Review --------------------------------------------------------------------------------


@pytest.fixture
def reviewed(community):
    Community.objects.filter(pk=community.pk).update(require_post_review=True)
    community.refresh_from_db()
    return community


def test_review_flow_approve(
    member, people, reviewed, broadcasts, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        post = services.create_post(
            actor=member, community=reviewed, kind=Post.Kind.DISCUSSION, title="T", body="B"
        )
    assert post.status == Post.Status.PENDING_REVIEW
    assert post.published_at is None
    assert broadcasts == []
    notified = set(
        Notification.objects.filter(category="review_request").values_list("recipient", flat=True)
    )
    assert notified == {people[role].pk for role in ("moderator", "animator", "owner")}

    assert _code(services.approve_review, actor=people["expert"], post=post) == "forbidden"
    with django_capture_on_commit_callbacks(execute=True):
        services.approve_review(actor=people["moderator"], post=post)
    post.refresh_from_db()
    assert post.status == Post.Status.PUBLISHED and post.published_at
    assert broadcasts == [(post.pk, "community_post")]
    assert AuditEvent.objects.filter(
        action="post.review_approved", community_id=reviewed.pk
    ).exists()
    assert Notification.objects.filter(recipient=member, category="system").exists()
    assert _code(services.approve_review, actor=people["moderator"], post=post) == "invalid_state"


def test_review_flow_reject(member, people, reviewed, django_capture_on_commit_callbacks):
    post = services.create_post(
        actor=member, community=reviewed, kind=Post.Kind.DISCUSSION, title="T", body="B"
    )
    moderator = people["moderator"]
    assert _code(services.reject_review, actor=moderator, post=post, note=" ") == "reason_required"
    with django_capture_on_commit_callbacks(execute=True):
        services.reject_review(actor=moderator, post=post, note="Too vague")
    post.refresh_from_db()
    assert post.status == Post.Status.DRAFT
    assert post.review_note == "Too vague"
    event = AuditEvent.objects.get(action="post.review_rejected")
    assert event.community_id == reviewed.pk and event.changes == {"note": "Too vague"}
    assert Notification.objects.filter(recipient=member, category="system").exists()
    # Re-submitting goes back to review and clears the note.
    services.publish_draft(actor=member, post=post)
    post.refresh_from_db()
    assert post.status == Post.Status.PENDING_REVIEW and post.review_note == ""


def test_moderator_post_skips_review(people, reviewed, broadcasts):
    post = services.create_post(
        actor=people["moderator"],
        community=reviewed,
        kind=Post.Kind.DISCUSSION,
        title="T",
        body="B",
    )
    assert post.status == Post.Status.PUBLISHED


def test_approval_refused_in_a_read_only_community(member, people, reviewed):
    post = services.create_post(
        actor=member, community=reviewed, kind=Post.Kind.DISCUSSION, title="T", body="B"
    )
    Community.objects.filter(pk=reviewed.pk).update(status=Community.Status.SUSPENDED)
    post.refresh_from_db()
    assert _code(services.approve_review, actor=people["moderator"], post=post) == "read_only"
    services.reject_review(actor=people["moderator"], post=post, note="Community suspended")
    post.refresh_from_db()
    assert post.status == Post.Status.DRAFT


# Drafts --------------------------------------------------------------------------------


def test_publish_and_delete_draft_rules(member, people, community, make_post, broadcasts):
    draft = make_post(community, member, status=Post.Status.DRAFT)
    assert _code(services.publish_draft, actor=people["owner"], post=draft) == "forbidden"
    assert _code(services.delete_draft, actor=people["owner"], post=draft) == "forbidden"
    published = make_post(community, member)
    assert _code(services.publish_draft, actor=member, post=published) == "invalid_state"
    assert _code(services.delete_draft, actor=member, post=published) == "forbidden"
    services.delete_draft(actor=member, post=draft)
    assert not Post.objects.filter(pk=draft.pk).exists()


def test_publish_draft_rechecks_the_kind(member, community, make_post):
    draft = make_post(community, member, status=Post.Status.DRAFT, kind=Post.Kind.ARTICLE)
    assert _code(services.publish_draft, actor=member, post=draft) == "kind_forbidden"


def test_drafts_are_read_only_in_a_suspended_community(member, community, make_post):
    draft = make_post(community, member, status=Post.Status.DRAFT)
    Community.objects.filter(pk=community.pk).update(status=Community.Status.SUSPENDED)
    draft.refresh_from_db()
    assert _code(services.publish_draft, actor=member, post=draft) == "read_only"
    assert _code(services.delete_draft, actor=member, post=draft) == "read_only"


# Editing -------------------------------------------------------------------------------


def _edit(post, actor, **extra):
    kwargs = {"title": post.title, "body": post.body, "tags": None, "version": post.version}
    kwargs.update(extra)
    return services.update_post(actor=actor, post=post, **kwargs)


def test_author_edits_a_discussion_without_revision(member, community, make_post):
    post = make_post(community, member)
    updated = _edit(post, member, title="New title", body="New *body* with keyword")
    assert updated.version == 2
    assert updated.title == "New title"
    assert "<em>body</em>" in updated.body_html
    assert not PostRevision.objects.filter(post=post).exists()
    assert Post.objects.filter(
        pk=post.pk, search_vector=SearchQuery("keyword", config="simple")
    ).exists()
    assert not AuditEvent.objects.filter(action="post.edited_by_moderator").exists()


def test_edit_conflict_on_stale_version(member, community, make_post):
    post = make_post(community, member)
    _edit(post, member, body="First")
    stale = Post.objects.get(pk=post.pk)
    stale.version = 1
    assert _code(_edit, post=stale, actor=member, body="Second", version=1) == "edit_conflict"
    post.refresh_from_db()
    assert post.body == "First"


@pytest.mark.parametrize("version", ["abc", None, "", "1.5"])
def test_garbage_version_is_an_edit_conflict(member, community, make_post, version):
    post = make_post(community, member)
    assert _code(_edit, post=post, actor=member, body="x", version=version) == "edit_conflict"


def test_unchanged_text_keeps_no_revision(member, moderator, community, make_post):
    post = make_post(community, member, title="Same", body="Same body")
    _edit(post, moderator, tags=["python"])
    assert not PostRevision.objects.exists()
    assert post.version == 2


def test_published_article_edit_keeps_a_revision(people, community, make_post):
    author = people["contributor"]
    post = make_post(community, author, kind=Post.Kind.ARTICLE, title="Old", body="Old body")
    _edit(post, author, body="New body")
    revision = PostRevision.objects.get(post=post)
    assert (revision.title, revision.body, revision.editor) == ("Old", "Old body", author)


def test_draft_article_edit_keeps_no_revision(people, community, make_post):
    author = people["contributor"]
    post = make_post(community, author, kind=Post.Kind.ARTICLE, status=Post.Status.DRAFT)
    _edit(post, author, body="New body")
    assert not PostRevision.objects.exists()


def test_moderator_edit_is_audited_revised_and_notified(
    member, moderator, community, make_post, django_capture_on_commit_callbacks
):
    post = make_post(community, member, title="Old", body="Old body")
    with django_capture_on_commit_callbacks(execute=True):
        _edit(post, moderator, title="Fixed")
    assert PostRevision.objects.get(post=post).editor == moderator
    event = AuditEvent.objects.get(action="post.edited_by_moderator")
    assert event.community_id == community.pk and event.actor == moderator
    assert Notification.objects.filter(recipient=member, category="system").exists()


def test_edit_permissions(member, people, community, make_post):
    post = make_post(community, member)
    assert _code(_edit, post=post, actor=people["expert"], body="x") == "forbidden"
    Post.objects.filter(pk=post.pk).update(status=Post.Status.HIDDEN)
    post.refresh_from_db()
    assert _code(_edit, post=post, actor=member, body="x") == "forbidden"
    assert _edit(post, people["moderator"], body="x").body == "x"
    assert _code(_edit, post=post, actor=member, body="x" * 50_001) == "forbidden"
    fresh = make_post(community, member)
    assert _code(_edit, post=fresh, actor=member, body="x" * 50_001) == "body_too_long"
    assert _code(_edit, post=fresh, actor=member, title="") == "title_required"
    Community.objects.filter(pk=community.pk).update(status=Community.Status.ARCHIVED)
    fresh.refresh_from_db()
    assert _code(_edit, post=fresh, actor=member, body="x") == "read_only"


def test_edit_updates_mentions_and_notifies_only_new_ones(
    member, people, community, make_post, django_capture_on_commit_callbacks
):
    post = make_post(community, member, body="Hi")
    with django_capture_on_commit_callbacks(execute=True):
        post = _edit(post, member, body="Hi @expert.person")
    with django_capture_on_commit_callbacks(execute=True):
        post = _edit(post, member, body="Hi @expert.person and @owner.person")
    assert Notification.objects.filter(recipient=people["expert"], category="mention").count() == 1
    assert Notification.objects.filter(recipient=people["owner"], category="mention").count() == 1
    assert '<span class="mention">@owner.person</span>' in post.body_html
    with django_capture_on_commit_callbacks(execute=True):
        post = _edit(post, member, body="Nobody @nobody.here")
    assert not Mention.objects.filter(post=post).exists()
    assert '<span class="mention">' not in post.body_html


def test_edit_with_tags(member, people, community, make_post):
    Tag.objects.create(name="Python", slug="python")
    post = make_post(community, member)
    post = _edit(post, member, tags=["python"])
    assert list(post.tags.values_list("name", flat=True)) == ["Python"]
    post = _edit(post, member)  # tags=None keeps them
    assert post.tags.count() == 1


# Pinning -------------------------------------------------------------------------------


def test_pin_limit_and_unpin(people, member, community, make_post, settings):
    settings.POSTS_PIN_LIMIT = 3
    animator = people["animator"]
    posts = [make_post(community, member, title=f"P{i}") for i in range(4)]
    assert _code(services.pin_post, actor=people["moderator"], post=posts[0]) == "forbidden"
    for post in posts[:3]:
        services.pin_post(actor=animator, post=post)
    assert _code(services.pin_post, actor=animator, post=posts[3]) == "pin_limit"
    assert _code(services.pin_post, actor=animator, post=posts[0]) == "invalid_state"
    services.unpin_post(actor=animator, post=posts[0])
    posts[0].refresh_from_db()
    assert posts[0].pinned_at is None and posts[0].pinned_by is None
    assert _code(services.unpin_post, actor=animator, post=posts[0]) == "invalid_state"
    services.pin_post(actor=animator, post=posts[3])
    posts[3].refresh_from_db()
    assert posts[3].pinned_by == animator
    assert AuditEvent.objects.filter(action="post.pinned", community_id=community.pk).count() == 4
    assert AuditEvent.objects.filter(action="post.unpinned").count() == 1


def test_pin_requires_a_published_post_and_an_active_community(
    people, member, community, make_post
):
    animator = people["animator"]
    draft = make_post(community, member, status=Post.Status.DRAFT)
    assert _code(services.pin_post, actor=animator, post=draft) == "invalid_state"
    post = make_post(community, member)
    Community.objects.filter(pk=community.pk).update(status=Community.Status.SUSPENDED)
    post.refresh_from_db()
    assert _code(services.pin_post, actor=animator, post=post) == "read_only"


# Hiding and archiving ------------------------------------------------------------------


def test_hide_and_unhide(
    member, moderator, people, community, make_post, django_capture_on_commit_callbacks
):
    post = make_post(community, member)
    services.pin_post(actor=people["animator"], post=post)
    assert _code(services.hide_post, actor=people["expert"], post=post, reason="x") == "forbidden"
    assert _code(services.hide_post, actor=moderator, post=post, reason="  ") == "reason_required"
    with django_capture_on_commit_callbacks(execute=True):
        services.hide_post(actor=moderator, post=post, reason="Confidential figures")
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN
    assert post.hidden_reason == "Confidential figures"
    assert post.pinned_at is None
    event = AuditEvent.objects.get(action="post.hidden")
    assert event.changes == {"reason": "Confidential figures"}
    assert event.community_id == community.pk
    assert Notification.objects.filter(recipient=member, category="system").count() == 1
    assert Post.objects.visible_to(member).filter(pk=post.pk).exists()
    assert _code(services.hide_post, actor=moderator, post=post, reason="again") == "invalid_state"

    assert _code(services.unhide_post, actor=people["expert"], post=post) == "forbidden"
    with django_capture_on_commit_callbacks(execute=True):
        services.unhide_post(actor=moderator, post=post)
    post.refresh_from_db()
    assert post.status == Post.Status.PUBLISHED
    assert AuditEvent.objects.filter(action="post.unhidden", actor=moderator).exists()
    assert Notification.objects.filter(recipient=member, category="system").count() == 2


def test_drafts_cannot_be_hidden(member, moderator, community, make_post):
    draft = make_post(community, member, status=Post.Status.DRAFT)
    assert _code(services.hide_post, actor=moderator, post=draft, reason="x") == "invalid_state"


def test_hiding_works_in_a_suspended_community(member, moderator, community, make_post):
    post = make_post(community, member)
    Community.objects.filter(pk=community.pk).update(status=Community.Status.SUSPENDED)
    post.refresh_from_db()
    services.hide_post(actor=moderator, post=post, reason="Spam")
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN


def test_archive(member, moderator, people, community, make_post):
    post = make_post(community, member)
    services.pin_post(actor=people["animator"], post=post)
    assert _code(services.archive_post, actor=member, post=post) == "forbidden"
    services.archive_post(actor=moderator, post=post)
    post.refresh_from_db()
    assert post.status == Post.Status.ARCHIVED
    assert post.archived_at is not None and post.pinned_at is None
    assert AuditEvent.objects.filter(action="post.archived", community_id=community.pk).exists()
    assert _code(services.archive_post, actor=moderator, post=post) == "invalid_state"


# Accepted answers ----------------------------------------------------------------------


@pytest.fixture
def question(make_post, community, member):
    return make_post(community, member, kind=Post.Kind.QUESTION)


def test_question_author_accepts_and_replaces_an_answer(
    question, member, people, make_comment, django_capture_on_commit_callbacks
):
    first = make_comment(question, people["expert"])
    second = make_comment(question, people["contributor"])
    with django_capture_on_commit_callbacks(execute=True):
        services.accept_answer(actor=member, post=question, comment=first)
    question.refresh_from_db()
    assert question.accepted_answer == first
    assert Notification.objects.filter(recipient=people["expert"], category="system").exists()
    services.accept_answer(actor=member, post=question, comment=second)
    question.refresh_from_db()
    assert question.accepted_answer == second
    services.clear_accepted_answer(actor=member, post=question)
    question.refresh_from_db()
    assert question.accepted_answer is None


def test_expert_may_accept_an_answer(question, people, make_comment):
    answer = make_comment(question, people["contributor"])
    services.accept_answer(actor=people["expert"], post=question, comment=answer)
    question.refresh_from_db()
    assert question.accepted_answer == answer
    services.clear_accepted_answer(actor=people["expert"], post=question)


def test_accept_answer_rules(question, member, people, community, make_post, make_comment):
    answer = make_comment(question, people["expert"])
    assert (
        _code(services.accept_answer, actor=people["contributor"], post=question, comment=answer)
        == "forbidden"
    )
    assert (
        _code(services.clear_accepted_answer, actor=people["contributor"], post=question)
        == "forbidden"
    )
    reply = make_comment(question, people["expert"], parent=answer)
    assert _code(services.accept_answer, actor=member, post=question, comment=reply) == (
        "invalid_state"
    )
    hidden = make_comment(question, people["expert"], status=Comment.Status.HIDDEN)
    assert _code(services.accept_answer, actor=member, post=question, comment=hidden) == (
        "invalid_state"
    )
    other = make_post(community, member, kind=Post.Kind.QUESTION)
    foreign = make_comment(other, people["expert"])
    assert _code(services.accept_answer, actor=member, post=question, comment=foreign) == (
        "invalid_state"
    )
    discussion = make_post(community, member)
    comment = make_comment(discussion, people["expert"])
    assert _code(services.accept_answer, actor=member, post=discussion, comment=comment) == (
        "invalid_state"
    )
    Community.objects.filter(pk=community.pk).update(status=Community.Status.ARCHIVED)
    question.refresh_from_db()
    assert _code(services.accept_answer, actor=member, post=question, comment=answer) == (
        "read_only"
    )


def test_accept_answer_rechecks_the_comment_under_lock(question, member, people, make_comment):
    answer = make_comment(question, people["expert"])
    Comment.objects.filter(pk=answer.pk).update(status=Comment.Status.HIDDEN)
    # ``answer`` in memory is still visible: the service must read the current row.
    assert _code(services.accept_answer, actor=member, post=question, comment=answer) == (
        "invalid_state"
    )


def test_accept_answer_refuses_before_checking_the_state(member, people, community, make_post,
                                                         make_comment):  # fmt: skip
    discussion = make_post(community, member)
    comment = make_comment(discussion, people["expert"])
    assert _code(services.accept_answer, actor=people["contributor"], post=discussion,
                 comment=comment) == "forbidden"  # fmt: skip


def test_system_notifications_only_reach_readers(
    make_community, make_user, add_member, make_post, make_comment,
    django_capture_on_commit_callbacks,
):  # fmt: skip
    private = make_community("Private", access_mode=Community.AccessMode.INVITE)
    author = make_user("author@example.com", first_name="Ann", last_name="Author")
    answerer = make_user("answer@example.com", first_name="Al", last_name="Answer")
    moderator = make_user("modo@example.com", first_name="Mo", last_name="Derator")
    for user in (author, answerer):
        add_member(private, user)
    add_member(private, moderator, Role.MODERATOR)
    question = make_post(private, author, kind=Post.Kind.QUESTION)
    answer = make_comment(question, answerer)
    hidden = make_post(private, author, status=Post.Status.HIDDEN, hidden_reason="r",
                       status_before_hidden=Post.Status.PUBLISHED)  # fmt: skip
    edited = make_post(private, author)
    CommunityMembership.objects.filter(community=private, user__in=[author, answerer]).delete()
    with django_capture_on_commit_callbacks(execute=True):
        services.accept_answer(actor=moderator, post=question, comment=answer)
        services.unhide_post(actor=moderator, post=hidden)
        _edit(edited, moderator, title="Fixed")
    assert not Notification.objects.filter(category="system").exists()


# Sharing -------------------------------------------------------------------------------


@pytest.fixture
def target(make_community):
    return make_community("Rust guild")


def test_share_creates_a_discussion_in_the_target(
    member, community, target, add_member, make_post, broadcasts
):
    add_member(target, member)
    original = make_post(community, member, title="Original", kind=Post.Kind.QUESTION)
    shared = services.share_post(
        actor=member,
        post=original,
        target_community=target,
        title="  My pick  ",
        comment="Worth a read",
    )
    assert shared.community == target
    assert shared.kind == Post.Kind.DISCUSSION
    assert shared.shared_from == original
    assert shared.title == "My pick"
    assert shared.status == Post.Status.PUBLISHED
    assert "Worth a read" in shared.body_html
    assert broadcasts == []  # on commit only


def test_share_never_copies_the_original_content(
    member, community, target, add_member, make_post, broadcasts
):
    """The original may be private: its title and body never reach the target community,
    not even through the search vector."""
    add_member(target, member)
    original = make_post(community, member, title="Confidential roadmap", body="Secret plans")
    shared = services.share_post(actor=member, post=original, target_community=target,
                                 title="Have a look")  # fmt: skip
    shared.refresh_from_db()
    assert (shared.title, shared.body) == ("Have a look", "")
    for word in ("confidential", "roadmap", "secret", "plans"):
        assert not Post.objects.filter(
            pk=shared.pk, search_vector=SearchQuery(word, config="simple")
        ).exists()
    assert _code(services.share_post, actor=member, post=original, target_community=target,
                 title="  ") == "title_required"  # fmt: skip


def test_share_goes_through_the_target_review(member, community, target, add_member, make_post,
                                              broadcasts):  # fmt: skip
    Community.objects.filter(pk=target.pk).update(require_post_review=True)
    target.refresh_from_db()
    add_member(target, member)
    original = make_post(community, member)
    shared = services.share_post(actor=member, post=original, target_community=target,
                                 title="Shared")  # fmt: skip
    assert shared.status == Post.Status.PENDING_REVIEW


def test_share_rules(member, outsider, community, target, add_member, make_post, make_community):
    original = make_post(community, member)
    assert _code(
        services.share_post, actor=member, post=original, target_community=target, title="T"
    ) == ("not_member")
    assert (
        _code(
            services.share_post, actor=member, post=original, target_community=community, title="T"
        )
        == "forbidden"
    )
    private = make_community("Secret", access_mode=Community.AccessMode.INVITE)
    hidden = make_post(private, None)
    add_member(target, outsider)
    assert (
        _code(services.share_post, actor=outsider, post=hidden, target_community=target, title="T")
        == "forbidden"
    )
    Community.objects.filter(pk=target.pk).update(status=Community.Status.SUSPENDED)
    target.refresh_from_db()
    add_member(target, member)
    assert _code(
        services.share_post, actor=member, post=original, target_community=target, title="T"
    ) == ("read_only")


def test_last_activity_bumped_on_approve(member, people, reviewed):
    post = services.create_post(
        actor=member, community=reviewed, kind=Post.Kind.DISCUSSION, title="T", body="B"
    )
    old = timezone.now() - timedelta(days=3)
    Community.objects.filter(pk=reviewed.pk).update(last_activity_at=old)
    Post.objects.filter(pk=post.pk).update(last_activity_at=old)
    post.refresh_from_db()
    services.approve_review(actor=people["moderator"], post=post)
    post.refresh_from_db()
    reviewed.refresh_from_db()
    assert post.last_activity_at > old
    assert reviewed.last_activity_at > old


def test_tags_given_as_instances_and_title_length(member, community, broadcasts):
    tag = Tag.objects.create(name="Go", slug="go")
    kwargs = {"actor": member, "community": community, "kind": Post.Kind.DISCUSSION, "body": "B"}
    post = services.create_post(title="T", tags=[tag, tag], **kwargs)
    assert list(post.tags.all()) == [tag]
    assert _code(services.create_post, title="x" * 201, **kwargs) == "title_too_long"


def test_tag_merged_away_meanwhile_is_refused(community, member):
    """The editor resolved a tag that a merge deleted before the save: a domain error, never
    a foreign key violation at commit."""
    ghost = get_or_create_tag("Ghost")
    Tag.objects.filter(pk=ghost.pk).delete()
    with pytest.raises(DomainError) as error:
        services.create_post(
            actor=member, community=community, kind=Post.Kind.DISCUSSION, title="T", body="",
            tags=[ghost],
        )  # fmt: skip
    assert error.value.code == "tag_unavailable"
