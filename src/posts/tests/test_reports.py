import pytest
from django.core.cache import cache
from django.utils import timezone

from audit.models import AuditEvent
from communities.models import Community, CommunityMembership
from core.errors import DomainError
from notifications.models import Notification
from posts import hiding
from posts import services_interactions as services
from posts.models import Comment, ContentReport, Post
from posts.services_moderation import decide_reports

pytestmark = pytest.mark.django_db

Role = CommunityMembership.Role


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _threshold(settings):
    settings.POSTS_REPORT_AUTOHIDE_THRESHOLD = 3


@pytest.fixture
def community(make_community):
    return make_community()


@pytest.fixture
def author(make_user, community, add_member):
    user = make_user("author@example.com", first_name="Ada", last_name="Author")
    add_member(community, user)
    return user


@pytest.fixture
def moderator(make_user, community, add_member):
    user = make_user("mod@example.com", first_name="Mona", last_name="Moderator")
    add_member(community, user, Role.MODERATOR)
    return user


@pytest.fixture
def readers(make_user):
    # Open community: reporting needs read access only.
    return [
        make_user(f"reader{index}@example.com", first_name="Reader", last_name=str(index))
        for index in range(4)
    ]


@pytest.fixture
def post(make_post, community, author):
    return make_post(community, author)


def _code(callable_, **kwargs):
    with pytest.raises(DomainError) as error:
        callable_(**kwargs)
    return error.value.code


def test_report_a_post(post, readers, moderator, community, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        report = services.report(actor=readers[0], target=post, reason="spam", details=" Ads ")
    assert report.post == post and report.comment is None
    assert report.community == community
    assert report.status == ContentReport.Status.OPEN
    assert report.details == "Ads"
    assert report.reporter == readers[0]
    assert Notification.objects.filter(category="moderation_alert", recipient=moderator).exists()


def test_one_open_report_per_user_and_target(post, readers):
    services.report(actor=readers[0], target=post, reason="spam")
    code = _code(services.report, actor=readers[0], target=post, reason="other")
    assert code == "already_reported"


def test_report_own_content_refused(post, author, make_comment):
    assert _code(services.report, actor=author, target=post, reason="spam") == "own_content"
    comment = make_comment(post, author)
    assert _code(services.report, actor=author, target=comment, reason="spam") == "own_content"


def test_report_validation(post, readers):
    assert _code(services.report, actor=readers[0], target=post, reason="boring") == (
        "invalid_reason"
    )
    code = _code(services.report, actor=readers[0], target=post, reason="other", details="x" * 2001)
    assert code == "body_too_long"


def test_report_needs_read_access(make_community, make_post, author, add_member, readers):
    private = make_community("Private", access_mode=Community.AccessMode.INVITE)
    add_member(private, author)
    secret = make_post(private, author)
    assert _code(services.report, actor=readers[0], target=secret, reason="spam") == "forbidden"


def test_report_in_read_only_community_is_allowed(post, readers):
    Community.objects.filter(pk=post.community_id).update(status=Community.Status.SUSPENDED)
    post.community.refresh_from_db()
    assert services.report(actor=readers[0], target=post, reason="spam").pk


def test_threshold_hides_post_provisionally(
    post, readers, moderator, author, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        for reader in readers[:2]:
            services.report(actor=reader, target=post, reason="spam")
    post.refresh_from_db()
    assert post.status == Post.Status.PUBLISHED

    with django_capture_on_commit_callbacks(execute=True):
        services.report(actor=readers[2], target=post, reason="spam")
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN
    assert post.hidden_by is None and post.hidden_reason == "automatic"
    event = AuditEvent.objects.get(action="post.auto_hidden")
    assert event.actor is None and event.community_id == post.community_id
    assert Notification.objects.filter(category="moderation_alert", recipient=moderator).exists()
    assert Notification.objects.filter(category="system", recipient=author).exists()
    # The hidden post is out of sight for readers (404 in the views).
    assert _code(services.report, actor=readers[3], target=post, reason="spam") == "forbidden"


def test_threshold_hides_comment_and_recounts(
    post, readers, make_comment, author, django_capture_on_commit_callbacks
):
    comment = make_comment(post, author)
    with django_capture_on_commit_callbacks(execute=True):
        for reader in readers[:3]:
            services.report(actor=reader, target=comment, reason="inappropriate")
    comment.refresh_from_db()
    assert comment.status == Comment.Status.HIDDEN
    assert AuditEvent.objects.filter(action="comment.auto_hidden").count() == 1
    post.refresh_from_db()
    assert post.comment_count == 0


def test_resolved_reports_do_not_count_towards_threshold(post, readers, moderator):
    first = services.report(actor=readers[0], target=post, reason="spam")
    services.dismiss_report(actor=moderator, report=first)
    services.report(actor=readers[0], target=post, reason="spam")  # a new open report
    services.report(actor=readers[1], target=post, reason="spam")
    post.refresh_from_db()
    assert post.status == Post.Status.PUBLISHED


def test_resolve_report_audited(post, readers, moderator):
    report = services.report(actor=readers[0], target=post, reason="outdated")
    services.resolve_report(actor=moderator, report=report, note="Updated by author")
    report.refresh_from_db()
    assert report.status == ContentReport.Status.RESOLVED
    assert report.handled_by == moderator and report.handled_at is not None
    assert report.resolution_note == "Updated by author"
    event = AuditEvent.objects.get(action="report.resolved")
    assert event.community_id == post.community_id and event.actor == moderator
    post.refresh_from_db()
    assert post.status == Post.Status.PUBLISHED


def test_resolve_report_with_hiding(post, readers, moderator):
    first = services.report(actor=readers[0], target=post, reason="confidential")
    second = services.report(actor=readers[1], target=post, reason="confidential")
    services.resolve_report(actor=moderator, report=first, note="Client data", hide=True)
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN
    assert post.hidden_by == moderator and post.hidden_reason == "Client data"
    assert AuditEvent.objects.get(action="post.hidden").changes == {"reason": "Client data"}
    # Every open report of the target is settled by the decision.
    second.refresh_from_db()
    assert second.status == ContentReport.Status.RESOLVED


def test_resolve_with_hiding_needs_a_reason(post, readers, moderator):
    report = services.report(actor=readers[0], target=post, reason="spam")
    code = _code(services.resolve_report, actor=moderator, report=report, hide=True)
    assert code == "reason_required"
    report.refresh_from_db()
    assert report.status == ContentReport.Status.OPEN


def test_resolve_with_hiding_confirms_an_auto_hidden_target(post, readers, moderator):
    reports = [services.report(actor=r, target=post, reason="spam") for r in readers[:3]]
    code = _code(services.resolve_report, actor=moderator, report=reports[0], hide=True)
    assert code == "reason_required"
    services.resolve_report(actor=moderator, report=reports[0], note="Spam", hide=True)
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN
    # The moderator's decision replaces the provisional hiding: dismissals no longer undo it.
    assert post.hidden_by == moderator and post.hidden_reason == "Spam"
    event = AuditEvent.objects.get(action="post.hidden")
    assert event.actor == moderator and event.changes == {"reason": "Spam"}
    assert event.community_id == post.community_id
    assert not ContentReport.objects.filter(status=ContentReport.Status.OPEN).exists()


def test_resolve_with_hiding_confirms_an_auto_hidden_comment(
    post, readers, moderator, author, make_comment
):
    comment = make_comment(post, author)
    reports = [services.report(actor=r, target=comment, reason="spam") for r in readers[:3]]
    services.resolve_report(actor=moderator, report=reports[1], note="Spam", hide=True)
    comment.refresh_from_db()
    assert comment.status == Comment.Status.HIDDEN and comment.hidden_by == moderator
    assert AuditEvent.objects.filter(action="comment.hidden", actor=moderator).exists()


def test_threshold_ignores_reports_of_deleted_reporters(post, readers, settings):
    settings.POSTS_REPORT_AUTOHIDE_THRESHOLD = 2
    for _ in range(2):
        ContentReport.objects.create(
            reporter=None, post=post, community=post.community, reason="spam"
        )
    services.report(actor=readers[0], target=post, reason="spam")
    post.refresh_from_db()
    assert post.status == Post.Status.PUBLISHED


def test_auto_hide_notification_only_for_an_author_who_can_read(
    make_community, make_user, make_post, make_comment, add_member, readers,
    django_capture_on_commit_callbacks,
):  # fmt: skip
    private = make_community("Private", access_mode=Community.AccessMode.INVITE)
    left = make_user("left@example.com", first_name="Lea", last_name="Left")
    for user in (left, *readers[:3]):
        add_member(private, user)
    post = make_post(private, left)
    comment = make_comment(post, left)
    CommunityMembership.objects.filter(community=private, user=left).delete()
    with django_capture_on_commit_callbacks(execute=True):
        for reader in readers[:3]:
            services.report(actor=reader, target=comment, reason="spam")
    comment.refresh_from_db()
    assert comment.status == Comment.Status.HIDDEN
    assert not Notification.objects.filter(category="system", recipient=left).exists()


def test_dismiss_report_audited(post, readers, moderator):
    report = services.report(actor=readers[0], target=post, reason="other")
    services.dismiss_report(actor=moderator, report=report, note="Fine")
    report.refresh_from_db()
    assert report.status == ContentReport.Status.DISMISSED
    assert report.resolution_note == "Fine"
    assert AuditEvent.objects.filter(
        action="report.dismissed", community_id=post.community_id
    ).exists()


def test_dismissing_every_open_report_restores_auto_hidden_target(post, readers, moderator):
    reports = [services.report(actor=r, target=post, reason="spam") for r in readers[:3]]
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN
    services.dismiss_report(actor=moderator, report=reports[0])
    services.dismiss_report(actor=moderator, report=reports[1])
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN
    services.dismiss_report(actor=moderator, report=reports[2])
    post.refresh_from_db()
    assert post.status == Post.Status.PUBLISHED
    assert AuditEvent.objects.filter(action="post.unhidden").exists()


def test_dismiss_does_not_restore_a_moderator_hide(post, readers, moderator):
    report = services.report(actor=readers[0], target=post, reason="spam")
    from posts.hiding import mark_hidden

    mark_hidden(post, actor=moderator, reason="Manual")
    services.dismiss_report(actor=moderator, report=report)
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN


def test_report_decisions_need_a_moderator_and_an_open_report(post, readers, author, moderator):
    report = services.report(actor=readers[0], target=post, reason="spam")
    assert _code(services.resolve_report, actor=author, report=report) == "forbidden"
    assert _code(services.dismiss_report, actor=readers[1], report=report) == "forbidden"
    services.dismiss_report(actor=moderator, report=report)
    assert _code(services.dismiss_report, actor=moderator, report=report) == "invalid_state"
    assert _code(services.resolve_report, actor=moderator, report=report) == "invalid_state"


def test_report_decisions_in_read_only_community(post, readers, moderator):
    report = services.report(actor=readers[0], target=post, reason="spam")
    Community.objects.filter(pk=post.community_id).update(status=Community.Status.ARCHIVED)
    report.community.refresh_from_db()
    services.resolve_report(actor=moderator, report=report, note="ok")
    report.refresh_from_db()
    assert report.status == ContentReport.Status.RESOLVED


def _pin(post, user):
    Post.objects.filter(pk=post.pk).update(pinned_at=timezone.now(), pinned_by=user)
    post.refresh_from_db()


def test_resolving_with_hiding_unpins_the_post(post, readers, moderator):
    _pin(post, moderator)
    content_report = services.report(actor=readers[0], target=post, reason="spam")
    services.resolve_report(actor=moderator, report=content_report, note="Off topic", hide=True)
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN
    assert (post.pinned_at, post.pinned_by) == (None, None)


def test_automatic_hiding_unpins_the_post(post, readers, moderator):
    _pin(post, moderator)
    for reader in readers[:3]:
        services.report(actor=reader, target=post, reason="spam")
    post.refresh_from_db()
    assert post.status == Post.Status.HIDDEN
    assert post.pinned_at is None


def test_queue_decision_reads_the_target_under_lock(
    post, readers, moderator, django_capture_on_commit_callbacks
):
    """A target hidden by another moderator after the queue page was read is not hidden (or
    notified) a second time: the decision re-reads it under its lock."""
    content_report = services.report(actor=readers[0], target=post, reason="spam")
    stale = ContentReport.objects.select_related("post").get(pk=content_report.pk)
    assert stale.post.status == Post.Status.PUBLISHED  # loaded before the hiding
    with django_capture_on_commit_callbacks(execute=True):
        hiding.mark_hidden(Post.objects.get(pk=post.pk), actor=moderator, reason="Spam")
    Notification.objects.all().delete()
    with django_capture_on_commit_callbacks(execute=True):
        decide_reports(actor=moderator, report=stale, decision="resolve_hide", note="Spam")
    assert not Notification.objects.filter(recipient=post.author, category="system").exists()
    content_report.refresh_from_db()
    assert content_report.status == ContentReport.Status.RESOLVED
