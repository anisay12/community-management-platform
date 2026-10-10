"""HTTP tests of the moderation queue (reports and pending posts) and of the tag
administration (list, search, merge)."""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from accounts.roles import Role
from audit.models import AuditEvent
from communities.models import AdminAccessGrant, Community
from communities.roles import CommunityRole
from core.tests.helpers import assert_single_h1
from notifications.models import Notification
from posts import selectors_moderation
from posts.models import Comment, ContentReport, Post
from taxonomy import services as tag_services
from taxonomy.models import Tag

pytestmark = pytest.mark.django_db

OPEN, REQUEST, INVITE = Community.AccessMode
S = Post.Status
REASON = ContentReport.Reason


@pytest.fixture
def community(make_community):
    return make_community("Python guild", access_mode=OPEN)


@pytest.fixture
def author(make_user, community, add_member):
    user = make_user("author@example.com", first_name="Ann", last_name="Author")
    add_member(community, user)
    return user


@pytest.fixture
def moderator(make_user, community, add_member):
    user = make_user("moderator@example.com", first_name="Mo", last_name="Derator")
    add_member(community, user, CommunityRole.MODERATOR)
    return user


@pytest.fixture
def reporters(make_user, community, add_member):
    users = []
    for index in range(3):
        user = make_user(f"reporter{index}@example.com", first_name="Rita", last_name=f"R{index}")
        add_member(community, user)
        users.append(user)
    return users


@pytest.fixture
def make_report():
    def _make(target, reporter, reason=REASON.SPAM, **extra):
        post = target.post if isinstance(target, Comment) else target
        field = "comment" if isinstance(target, Comment) else "post"
        return ContentReport.objects.create(
            reporter=reporter, community=post.community, reason=reason, **{field: target}, **extra
        )

    return _make


def _queue(community, tab=None):
    url = reverse("posts:moderation_queue", args=[community.slug])
    return f"{url}?tab={tab}" if tab else url


def _decide(report):
    return reverse("posts:report_decide", args=[report.public_id])


def _review(post):
    return reverse("posts:review_decide", args=[post.community.slug, post.public_id])


def _queue_decision(decision, **extra):
    """What the review form of the queue posts (the note is typed inline: confirmed)."""
    return {"decision": decision, "from": "queue", "confirmed": "1", **extra}


def _staff(make_user, email, role):
    user = make_user(email)
    user.groups.add(Group.objects.get(name=role))
    return user


# --- Queue access --------------------------------------------------------------------------


def test_queue_requires_login(client, community):
    assert client.get(_queue(community)).status_code == 302


@pytest.mark.parametrize(
    ("role", "status"),
    [
        (CommunityRole.MEMBER, 403),
        (CommunityRole.EXPERT, 403),
        (CommunityRole.MODERATOR, 200),
        (CommunityRole.ANIMATOR, 200),
        (CommunityRole.OWNER, 200),
    ],
)
def test_queue_access_by_community_role(client, make_user, community, add_member, role, status):
    user = make_user("someone@example.com")
    add_member(community, user, role)
    client.force_login(user)
    assert client.get(_queue(community)).status_code == status


def test_queue_forbidden_to_non_member_of_open_community(client, make_user, community):
    client.force_login(make_user("outsider@example.com"))
    assert client.get(_queue(community)).status_code == 403


@pytest.mark.parametrize(("mode", "listed"), [(REQUEST, True), (INVITE, False)])
def test_queue_hidden_from_non_member_of_private_community(
    client, make_user, make_community, mode, listed
):
    private = make_community("Private", access_mode=mode, listed=listed)
    client.force_login(make_user("outsider@example.com"))
    assert client.get(_queue(private)).status_code == 404


@pytest.mark.parametrize("role", [Role.TECHNICAL_ADMIN, Role.AUDITOR])
def test_queue_forbidden_to_technical_admin_and_auditor(
    client, make_user, verified_login, community, role
):
    verified_login(client, _staff(make_user, "staff@example.com", role))
    assert client.get(_queue(community)).status_code == 403


def test_functional_admin_needs_a_grant_on_private_community(
    client, functional_admin, verified_login, make_community
):
    private = make_community("Private", access_mode=REQUEST)
    verified_login(client, functional_admin)
    assert client.get(_queue(private)).status_code == 403
    AdminAccessGrant.objects.create(community=private, user=functional_admin, reason="Check")
    assert client.get(_queue(private)).status_code == 200


def test_functional_admin_moderates_open_community(
    client, functional_admin, verified_login, community
):
    verified_login(client, functional_admin)
    response = client.get(_queue(community))
    assert response.status_code == 200
    assert_single_h1(response)


@pytest.mark.parametrize("status", [Community.Status.SUSPENDED, Community.Status.ARCHIVED])
def test_queue_works_whatever_the_community_status(
    client, community, moderator, author, reporters, make_post, make_report, status
):
    post = make_post(community, author, title="Spammy")
    report = make_report(post, reporters[0])
    Community.objects.filter(pk=community.pk).update(status=status)
    client.force_login(moderator)
    assert "Spammy" in client.get(_queue(community)).content.decode()
    response = client.post(_decide(report), {"decision": "dismiss", "note": ""})
    assert response.status_code == 302
    report.refresh_from_db()
    assert report.status == ContentReport.Status.DISMISSED


# --- Reports tab ---------------------------------------------------------------------------


def test_reports_grouped_by_target_open_first(
    client, community, moderator, author, reporters, make_post, make_comment, make_report
):
    post = make_post(community, author, title="Reported post")
    comment = make_comment(post, author, body="A rude remark")
    handled = make_post(community, author, title="Old case")
    make_report(post, reporters[0], REASON.SPAM, details="Buy now")
    make_report(post, reporters[1], REASON.OUTDATED)
    make_report(comment, reporters[2], REASON.INAPPROPRIATE)
    make_report(handled, reporters[0], status=ContentReport.Status.DISMISSED)
    client.force_login(moderator)
    response = client.get(_queue(community))
    assert response.status_code == 200
    groups = response.context["page_obj"].object_list
    assert [group.is_open for group in groups] == [True, True, False]
    by_target = {group.target.pk if group.is_post else -group.target.pk: group for group in groups}
    post_group = by_target[post.pk]
    assert post_group.report_count == 2
    assert {report.reason for report in post_group.reports} == {REASON.SPAM, REASON.OUTDATED}
    html = response.content.decode()
    assert html.count(">Reported post</a>") == 1
    assert "Buy now" in html
    assert "A rude remark" in html
    assert "Spam" in html and "Outdated information" in html
    assert "Old case" in html
    # The open count is shown on the tab.
    assert response.context["reported_count"] == 2


def test_auto_hidden_target_is_flagged(
    client, community, moderator, author, reporters, make_post, make_report
):
    post = make_post(
        community, author, title="Auto", status=S.HIDDEN, hidden_reason="automatic",
        status_before_hidden=S.PUBLISHED,
    )  # fmt: skip
    for reporter in reporters:
        make_report(post, reporter)
    client.force_login(moderator)
    html = client.get(_queue(community)).content.decode()
    assert "Hidden automatically after several reports" in html


def test_resolve_and_hide_hides_target_audits_and_notifies(
    client, community, moderator, author, reporters, make_post, make_report,
    django_capture_on_commit_callbacks,
):  # fmt: skip
    post = make_post(community, author, title="Bad")
    first = make_report(post, reporters[0])
    second = make_report(post, reporters[1])
    client.force_login(moderator)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(_decide(first), {"decision": "resolve_hide", "note": "Spam link"})
    assert response.status_code == 302
    assert response.url == _queue(community)
    post.refresh_from_db()
    assert post.status == S.HIDDEN
    assert post.hidden_by == moderator
    assert post.hidden_reason == "Spam link"
    for report in (first, second):
        report.refresh_from_db()
        assert report.status == ContentReport.Status.RESOLVED
    assert AuditEvent.objects.filter(action="post.hidden", actor=moderator).count() == 1
    assert AuditEvent.objects.filter(action="report.resolved").count() == 2
    assert Notification.objects.filter(recipient=author, category="system").exists()


def test_resolve_and_hide_confirms_auto_hidden_comment(
    client, community, moderator, author, reporters, make_post, make_comment, make_report,
    django_capture_on_commit_callbacks,
):  # fmt: skip
    post = make_post(community, author)
    comment = make_comment(post, author, status=Comment.Status.HIDDEN, hidden_reason="automatic")
    reports = [make_report(comment, reporter) for reporter in reporters]
    client.force_login(moderator)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(_decide(reports[0]), {"decision": "resolve_hide", "note": "Insults"})
    comment.refresh_from_db()
    assert comment.hidden_by == moderator
    assert comment.hidden_reason == "Insults"
    assert not ContentReport.objects.filter(status=ContentReport.Status.OPEN).exists()
    assert AuditEvent.objects.filter(action="comment.hidden").count() == 1
    assert Notification.objects.filter(recipient=author, category="system").exists()


def test_hide_requires_a_note(client, community, moderator, author, reporters, make_post,
                              make_report):  # fmt: skip
    post = make_post(community, author)
    report = make_report(post, reporters[0])
    client.force_login(moderator)
    response = client.post(_decide(report), {"decision": "resolve_hide", "note": " "})
    # The decision form is shown again (no redirect), with the error and the typed note.
    assert response.status_code == 200
    assert_single_h1(response)
    html = response.content.decode()
    assert "Give a reason for hiding this content." in html
    assert f'action="{_decide(report)}"' in html
    assert post.title in html
    post.refresh_from_db()
    report.refresh_from_db()
    assert post.status == S.PUBLISHED
    assert report.status == ContentReport.Status.OPEN


def test_dismiss_closes_every_open_report_and_restores_auto_hidden(
    client, community, moderator, author, reporters, make_post, make_report
):
    post = make_post(
        community, author, status=S.HIDDEN, hidden_reason="automatic",
        status_before_hidden=S.PUBLISHED,
    )  # fmt: skip
    reports = [make_report(post, reporter) for reporter in reporters]
    client.force_login(moderator)
    client.post(_decide(reports[1]), {"decision": "dismiss", "note": "Fine"})
    post.refresh_from_db()
    assert post.status == S.PUBLISHED
    for report in reports:
        report.refresh_from_db()
        assert report.status == ContentReport.Status.DISMISSED
        assert report.resolution_note == "Fine"
    assert AuditEvent.objects.filter(action="report.dismissed").count() == 3
    assert AuditEvent.objects.filter(action="post.unhidden", actor=moderator).count() == 1


def test_resolve_without_hiding(client, community, moderator, author, reporters, make_post,
                                make_report):  # fmt: skip
    post = make_post(community, author)
    report = make_report(post, reporters[0])
    client.force_login(moderator)
    client.post(_decide(report), {"decision": "resolve", "note": "Author warned"})
    report.refresh_from_db()
    post.refresh_from_db()
    assert report.status == ContentReport.Status.RESOLVED
    assert report.handled_by == moderator
    assert post.status == S.PUBLISHED


def test_decide_already_handled_report_shows_error(
    client, community, moderator, author, reporters, make_post, make_report
):
    post = make_post(community, author)
    report = make_report(post, reporters[0], status=ContentReport.Status.DISMISSED)
    client.force_login(moderator)
    response = client.post(_decide(report), {"decision": "resolve", "note": ""})
    assert response.status_code == 302
    report.refresh_from_db()
    assert report.status == ContentReport.Status.DISMISSED


def test_decide_access(client, make_user, community, author, reporters, make_post, make_report,
                       make_community, add_member):  # fmt: skip
    post = make_post(community, author)
    report = make_report(post, reporters[0])
    client.force_login(reporters[1])
    assert client.post(_decide(report), {"decision": "dismiss"}).status_code == 403
    private = make_community("Private", access_mode=INVITE)
    add_member(private, author)
    private_report = make_report(make_post(private, author), reporters[0])
    assert client.post(_decide(private_report), {"decision": "dismiss"}).status_code == 404
    report.refresh_from_db()
    assert report.status == ContentReport.Status.OPEN


def test_decide_is_post_only_and_validates(client, community, moderator, author, reporters,
                                           make_post, make_report):  # fmt: skip
    report = make_report(make_post(community, author), reporters[0])
    client.force_login(moderator)
    assert client.get(_decide(report)).status_code == 405
    assert client.post(_decide(report), {"decision": "nuke"}).status_code == 400


def test_reports_tab_query_ceiling(
    client, community, moderator, author, reporters, make_post, make_comment, make_report,
    django_assert_max_num_queries,
):  # fmt: skip
    for index in range(12):
        post = make_post(community, author, title=f"Post {index}")
        comment = make_comment(post, author)
        make_report(post, reporters[0])
        make_report(post, reporters[1], REASON.OTHER)
        make_report(comment, reporters[2])
    client.force_login(moderator)
    with django_assert_max_num_queries(17):
        response = client.get(_queue(community))
    assert response.status_code == 200
    assert len(response.context["page_obj"].object_list) == 20


def test_report_history_is_bounded_per_target(
    client, community, moderator, author, make_post, make_user, make_report
):
    post = make_post(community, author)
    for index in range(14):
        reporter = make_user(f"old{index}@example.com")
        make_report(post, reporter, status=ContentReport.Status.DISMISSED, details=f"Old {index}")
    client.force_login(moderator)
    response = client.get(_queue(community))
    (group,) = response.context["page_obj"].object_list
    assert group.report_count == 14
    assert len(group.reports) == selectors_moderation.REPORTS_SHOWN_PER_GROUP
    html = response.content.decode()
    assert "Old 13" in html and "Old 4" in html  # the ten most recent ones
    assert "Old 3" not in html and "Old 0" not in html
    assert "4 earlier reports not shown" in html


# --- Awaiting review tab -------------------------------------------------------------------


def test_review_tab_lists_pending_posts(client, community, moderator, author, make_post):
    make_post(community, author, title="Please review", status=S.PENDING_REVIEW)
    make_post(community, author, title="Already out")
    client.force_login(moderator)
    response = client.get(_queue(community, "review"))
    html = response.content.decode()
    assert "Please review" in html
    assert "Already out" not in html
    assert response.context["pending_count"] == 1
    # The tab counts have a text alternative (the badge alone is a bare number).
    assert '<span class="visually-hidden"> (1 open item)</span>' in html


def test_approve_publishes_audits_and_notifies(
    client, community, moderator, author, make_post, django_capture_on_commit_callbacks
):
    post = make_post(community, author, status=S.PENDING_REVIEW)
    client.force_login(moderator)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(_review(post), _queue_decision("approve"))
    assert response.status_code == 302
    assert response.url == _queue(community, "review")
    post.refresh_from_db()
    assert post.status == S.PUBLISHED
    assert AuditEvent.objects.filter(action="post.review_approved", actor=moderator).exists()
    assert Notification.objects.filter(recipient=author, category="system").exists()


def test_reject_needs_a_note(client, community, moderator, author, make_post,
                             django_capture_on_commit_callbacks):  # fmt: skip
    post = make_post(community, author, status=S.PENDING_REVIEW)
    client.force_login(moderator)
    response = client.post(_review(post), _queue_decision("reject", note="  "))
    # The note form is shown again (not a redirect), still returning to the queue.
    assert response.status_code == 200
    html = response.content.decode()
    assert "Explain to the author why the post is refused." in html
    assert 'name="from" value="queue"' in html
    assert _queue(community, "review") in html
    post.refresh_from_db()
    assert post.status == S.PENDING_REVIEW
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(_review(post), _queue_decision("reject", note="Add sources"))
    assert response.url == _queue(community, "review")
    post.refresh_from_db()
    assert (post.status, post.review_note) == (S.DRAFT, "Add sources")
    assert AuditEvent.objects.filter(action="post.review_rejected").exists()
    assert Notification.objects.filter(recipient=author, category="system").exists()


def test_review_decide_access(client, make_user, community, author, make_post, add_member):
    post = make_post(community, author, status=S.PENDING_REVIEW)
    member = make_user("member@example.com")
    add_member(community, member)
    client.force_login(member)
    # A pending post is invisible to ordinary members: 404.
    assert client.post(_review(post), {"decision": "approve"}).status_code == 404
    client.force_login(author)
    assert client.post(_review(post), {"decision": "approve"}).status_code == 403
    assert client.get(_review(post)).status_code == 405
    post.refresh_from_db()
    assert post.status == S.PENDING_REVIEW


def test_review_form_of_the_queue_posts_to_the_post_review_action(
    client, community, moderator, author, make_post
):
    post = make_post(community, author, status=S.PENDING_REVIEW)
    client.force_login(moderator)
    html = client.get(_queue(community, "review")).content.decode()
    assert f'action="{_review(post)}"' in html
    assert 'name="from" value="queue"' in html


def test_queue_decision_on_a_post_no_longer_pending_returns_to_the_queue(
    client, community, moderator, author, make_post
):
    post = make_post(community, author)
    client.force_login(moderator)
    for decision in ("approve", "reject"):
        response = client.post(_review(post), _queue_decision(decision, note="Late"))
        assert response.status_code == 302
        assert response.url == _queue(community, "review")
    page = client.get(_queue(community, "review"))
    toasts = [str(message) for message in page.context["messages"]]
    assert "This action is not possible in the current state." in toasts


def test_review_tab_query_ceiling(
    client, community, moderator, author, make_post, django_assert_max_num_queries
):
    for index in range(25):
        make_post(community, author, title=f"Pending {index}", status=S.PENDING_REVIEW)
    client.force_login(moderator)
    with django_assert_max_num_queries(14):
        response = client.get(_queue(community, "review"))
    assert len(response.context["page_obj"].object_list) == 20


def test_review_tab_is_paginated(client, community, moderator, author, make_post):
    for index in range(21):
        make_post(community, author, title=f"Pending {index}", status=S.PENDING_REVIEW)
    client.force_login(moderator)
    response = client.get(_queue(community, "review") + "&page=2")
    assert len(response.context["page_obj"].object_list) == 1
    assert "tab=review&amp;page=1" in response.content.decode()


# --- Moderation link -----------------------------------------------------------------------


def test_moderation_link_on_feed_for_moderators_only(
    client, community, moderator, author, reporters, make_post, make_report
):
    post = make_post(community, author)
    make_report(post, reporters[0])
    make_post(community, author, status=S.PENDING_REVIEW)
    feed = reverse("posts:feed", args=[community.slug])
    client.force_login(moderator)
    html = client.get(feed).content.decode()
    assert _queue(community) in html
    assert "2 open items" in html
    client.force_login(reporters[0])
    assert _queue(community) not in client.get(feed).content.decode()


# --- Tag administration --------------------------------------------------------------------


@pytest.fixture
def admin_client(client, functional_admin, verified_login):
    return verified_login(client, functional_admin)


def test_tag_list_for_functional_admin(admin_client, community, author, make_post):
    tag = tag_services.get_or_create_tag("Données")
    make_post(community, author).tags.add(tag)
    community.tags.add(tag)
    response = admin_client.get(reverse("manage:tag_list"))
    assert response.status_code == 200
    assert_single_h1(response)
    row = next(t for t in response.context["page_obj"].object_list if t.pk == tag.pk)
    assert (row.post_count, row.community_count) == (1, 1)
    html = response.content.decode()
    assert "Données" in html and "donnees" in html
    users_page = admin_client.get(reverse("manage:user_list")).content.decode()
    assert f'href="{reverse("manage:tag_list")}"' in users_page  # "Tags" administration tab


def test_tag_list_search(admin_client):
    tag_services.get_or_create_tag("Python")
    tag_services.get_or_create_tag("Données")
    response = admin_client.get(reverse("manage:tag_list"), {"q": "DONNEES"})
    assert [tag.name for tag in response.context["page_obj"].object_list] == ["Données"]


def test_tag_admin_sends_anonymous_users_to_login(client):
    for url in (reverse("manage:tag_list"), reverse("manage:tag_merge")):
        response = client.post(url)
        assert response.status_code == 302
        assert response["Location"] == f"{reverse('accounts:login')}?next={url}"


@pytest.mark.parametrize("role", [None, Role.AUDITOR, Role.TECHNICAL_ADMIN])
def test_tag_admin_hidden_from_others(client, make_user, verified_login, role):
    user = make_user("someone@example.com")
    if role:
        user.groups.add(Group.objects.get(name=role))
        verified_login(client, user)
    else:
        client.force_login(user)
    source = tag_services.get_or_create_tag("ML")
    target = tag_services.get_or_create_tag("AI")
    assert client.get(reverse("manage:tag_list")).status_code == 404
    response = client.post(
        reverse("manage:tag_merge"), {"source": "ML", "target": "AI", "confirm": "1"}
    )
    assert response.status_code == 404
    assert Tag.objects.filter(pk__in=[source.pk, target.pk]).count() == 2


def test_tag_merge_confirmation_then_merge(admin_client, functional_admin, community, author,
                                           make_post):  # fmt: skip
    source = tag_services.get_or_create_tag("ML")
    target = tag_services.get_or_create_tag("Machine learning")
    make_post(community, author).tags.add(source)
    url = reverse("manage:tag_merge")
    response = admin_client.post(url, {"source": "ml", "target": "machine LEARNING"})
    assert response.status_code == 200
    assert response.context["source"] == source
    assert response.context["post_count"] == 1
    assert Tag.objects.filter(pk=source.pk).exists()
    response = admin_client.post(
        url, {"source": source.name, "target": target.name, "confirm": "1"}
    )
    assert response.status_code == 302
    assert response.url == reverse("manage:tag_list")
    assert not Tag.objects.filter(pk=source.pk).exists()
    assert AuditEvent.objects.filter(action="tag.merged", actor=functional_admin).exists()


@pytest.mark.parametrize(
    ("source", "target", "message"),
    [
        ("Nope", "AI", "No tag named"),
        ("AI", "ai", "Choose two different tags."),
    ],
)
def test_tag_merge_form_errors(admin_client, source, target, message):
    tag_services.get_or_create_tag("AI")
    response = admin_client.post(reverse("manage:tag_merge"), {"source": source, "target": target})
    assert response.status_code == 400
    assert message in response.content.decode()
    assert Tag.objects.count() == 1


def test_tag_merge_is_post_only(admin_client):
    assert admin_client.get(reverse("manage:tag_merge")).status_code == 405
