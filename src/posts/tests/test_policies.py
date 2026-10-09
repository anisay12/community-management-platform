"""Policy matrix: role x action x community status, plus the author-specific rules."""

import pytest
from django.contrib.auth.models import Group

from accounts.models import User
from accounts.roles import Role
from communities.models import Community
from posts import policies
from posts.models import Comment, Post

pytestmark = pytest.mark.django_db

ROLES = [
    "non_member",
    "member",
    "contributor",
    "expert",
    "moderator",
    "animator",
    "owner",
    "functional_admin",
    "technical_admin",
    "suspended",
]
COMMUNITY_ROLES = ["member", "contributor", "expert", "moderator", "animator", "owner"]
STATUSES = list(Community.Status)


def at_least(minimum):
    return set(COMMUNITY_ROLES[COMMUNITY_ROLES.index(minimum) :])


MEMBERS = at_least("member")
READERS = MEMBERS | {"non_member", "functional_admin"}  # open community


def live(roles):
    return {status: (roles if status == "active" else set()) for status in STATUSES}


def readable(roles):
    """Allowed while the community is readable (archived: members only)."""
    return {
        "active": roles,
        "suspended": roles,
        "archived": roles & MEMBERS,
    }


# policy name -> {community status -> roles allowed}; targets are another member's content.
MATRIX = {
    "create_discussion": live(MEMBERS),
    "create_question": live(MEMBERS),
    "create_article": live(at_least("contributor")),
    "create_announcement": live(at_least("animator") | {"functional_admin"}),
    # Decision 9 clarification: moderation works whatever the community status.
    "moderate": readable(at_least("moderator") | {"functional_admin"}),
    "review": readable(at_least("moderator") | {"functional_admin"}),
    "pin": live(at_least("animator") | {"functional_admin"}),
    "create_tag": live(at_least("contributor") | {"functional_admin"}),
    "merge_tags": {status: {"functional_admin"} for status in STATUSES},
    "edit_post": live(at_least("moderator") | {"functional_admin"}),
    "accept_answer": live(at_least("moderator") | {"functional_admin"}),
    "view_revisions": readable(at_least("moderator") | {"functional_admin"}),
    "comment": live(MEMBERS),
    "react_post": live(MEMBERS),
    "react_comment": live(MEMBERS),
    "edit_comment": {status: set() for status in STATUSES},
    "delete_draft": {status: set() for status in STATUSES},
    "bookmark": readable(READERS),
    "report_post": readable(READERS),
    "report_comment": readable(READERS),
    "view_post": readable(READERS),
}


def evaluate(name, user, community, post, comment, draft):
    calls = {
        "create_discussion": lambda: policies.can_create_post(user, community, "discussion"),
        "create_question": lambda: policies.can_create_post(user, community, "question"),
        "create_article": lambda: policies.can_create_post(user, community, "article"),
        "create_announcement": lambda: policies.can_create_post(user, community, "announcement"),
        "moderate": lambda: policies.can_moderate(user, community),
        "review": lambda: policies.can_review(user, community),
        "pin": lambda: policies.can_pin(user, community),
        "create_tag": lambda: policies.can_create_tag(user, community),
        "merge_tags": lambda: policies.can_merge_tags(user),
        "edit_post": lambda: policies.can_edit_post(user, post),
        "accept_answer": lambda: policies.can_accept_answer(user, post),
        "view_revisions": lambda: policies.can_view_revisions(user, post),
        "comment": lambda: policies.can_comment(user, post),
        "react_post": lambda: policies.can_react(user, post),
        "react_comment": lambda: policies.can_react(user, comment),
        "edit_comment": lambda: policies.can_edit_comment(user, comment),
        "delete_draft": lambda: policies.can_delete_draft(user, draft),
        "bookmark": lambda: policies.can_bookmark(user, post),
        "report_post": lambda: policies.can_report(user, post),
        "report_comment": lambda: policies.can_report(user, comment),
        "view_post": lambda: policies.can_view_post(user, post),
    }
    return calls[name]()


@pytest.fixture
def setup(make_user, make_community, add_member, make_post, make_comment):
    def _build(status):
        community = make_community("Guild", status=status)
        author = make_user("author@example.com", first_name="Ann")
        add_member(community, author)
        post = make_post(community, author, kind=Post.Kind.QUESTION)
        comment = make_comment(post, author)
        draft = make_post(community, author, status=Post.Status.DRAFT)
        users = {"non_member": make_user("outsider@example.com")}
        for role in COMMUNITY_ROLES:
            users[role] = make_user(f"{role}@example.com")
            add_member(community, users[role], role=role)
        for name, group in (
            ("functional_admin", Role.FUNCTIONAL_ADMIN),
            ("technical_admin", Role.TECHNICAL_ADMIN),
        ):
            users[name] = make_user(f"{name}@example.com")
            users[name].groups.add(Group.objects.get(name=group))
        suspended = make_user("suspended@example.com")
        add_member(community, suspended, role="owner")
        User.objects.filter(pk=suspended.pk).update(status=User.Status.SUSPENDED)
        users["suspended"] = User.objects.get(pk=suspended.pk)
        return community, users, post, comment, draft

    return _build


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("policy", list(MATRIX))
def test_policy_matrix(setup, status, policy):
    community, users, post, comment, draft = setup(status)
    for role in ROLES:
        want = role in MATRIX[policy][status]
        got = evaluate(policy, users[role], community, post, comment, draft)
        assert got is want, (policy, status, role)


@pytest.fixture
def scene(make_user, make_community, add_member, make_post, make_comment):
    community = make_community("Guild")
    author = make_user("author@example.com", first_name="Ann")
    add_member(community, author)
    post = make_post(community, author, kind=Post.Kind.QUESTION)
    comment = make_comment(post, author)
    return community, author, post, comment


def test_authors_and_their_own_content(scene, make_post):
    community, author, post, comment = scene
    assert policies.can_edit_post(author, post)
    assert policies.can_edit_comment(author, comment)
    assert policies.can_accept_answer(author, post)
    assert not policies.can_react(author, post)
    assert not policies.can_react(author, comment)
    assert not policies.can_report(author, post)
    assert not policies.can_report(author, comment)
    assert policies.can_bookmark(author, post)
    draft = make_post(community, author, status=Post.Status.DRAFT)
    assert policies.can_delete_draft(author, draft)
    assert not policies.can_delete_draft(author, post)
    assert not policies.can_comment(author, draft)


@pytest.mark.parametrize("status", [Community.Status.SUSPENDED, Community.Status.ARCHIVED])
def test_read_only_community_refuses_draft_deletion(scene, make_post, status):
    community, author, _post, _comment = scene
    draft = make_post(community, author, status=Post.Status.DRAFT)
    community.status = status
    assert not policies.can_delete_draft(author, draft)


def test_hidden_and_archived_content_is_frozen(scene, make_user, add_member):
    community, author, post, comment = scene
    moderator = make_user("mod@example.com")
    add_member(community, moderator, role="moderator")
    reader = make_user("reader@example.com")
    add_member(community, reader)
    post.status = Post.Status.HIDDEN
    assert not policies.can_edit_post(author, post)
    assert policies.can_edit_post(moderator, post)
    assert not policies.can_comment(reader, post)
    assert not policies.can_react(reader, post)
    assert not policies.can_report(reader, post)
    post.status = Post.Status.ARCHIVED
    assert not policies.can_edit_post(moderator, post)
    assert not policies.can_react(reader, post)
    assert policies.can_bookmark(reader, post)
    assert not policies.can_report(reader, comment)
    post.status = Post.Status.PUBLISHED
    comment.status = Comment.Status.HIDDEN
    assert not policies.can_react(reader, comment)
    assert not policies.can_edit_comment(author, comment)
    assert not policies.can_view_comment(reader, comment)
    assert policies.can_view_comment(author, comment)
    assert policies.can_view_comment(moderator, comment)


def test_accept_answer_only_on_published_questions(scene):
    _community, author, post, _comment = scene
    post.kind = Post.Kind.DISCUSSION
    assert not policies.can_accept_answer(author, post)
    post.kind = Post.Kind.QUESTION
    post.status = Post.Status.ARCHIVED
    assert not policies.can_accept_answer(author, post)


def test_share_needs_the_original_and_creation_rights(scene, make_user, make_community, add_member):
    community, _author, post, _comment = scene
    target = make_community("Other guild")
    member = make_user("member@example.com")
    add_member(community, member)
    assert not policies.can_share(member, post, target)  # not a member of the target
    add_member(target, member)
    member = User.objects.get(pk=member.pk)  # drop the memoised memberships
    assert policies.can_share(member, post, target)
    assert not policies.can_share(member, post, community)  # same community
    private = make_community("Private", access_mode=Community.AccessMode.INVITE)
    hidden_post = Post.objects.create(
        community=private, author=None, author_display="X", title="T", body="B", status="published"
    )
    assert not policies.can_share(member, hidden_post, target)
    post.status = Post.Status.DRAFT
    assert not policies.can_share(member, post, target)


def test_unknown_kind_cannot_be_created(scene):
    community, author, _post, _comment = scene
    assert not policies.can_create_post(author, community, "poll")
