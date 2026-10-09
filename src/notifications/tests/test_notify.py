import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from notifications.models import Notification
from notifications.services import notify

pytestmark = pytest.mark.django_db


def test_rows_created_only_after_commit(make_user, django_capture_on_commit_callbacks):
    actor = make_user("actor@example.com")
    alice = make_user("alice2@example.com")
    with django_capture_on_commit_callbacks() as callbacks:
        notify("community.invited", [alice], actor=actor, target=("community", "abc"))
        assert Notification.objects.count() == 0
    assert len(callbacks) == 1
    with CaptureQueriesContext(connection) as queries:
        callbacks[0]()
    assert len(queries) == 1
    row = Notification.objects.get()
    assert (row.recipient, row.actor, row.category) == (alice, actor, "community.invited")
    assert (row.target_type, row.target_id, row.read_at) == ("community", "abc", None)


def test_skips_actor_inactive_and_duplicate_recipients(
    make_user, django_capture_on_commit_callbacks
):
    actor = make_user("actor@example.com")
    alice = make_user("alice2@example.com")
    pending = make_user("pending@example.com", status=User.Status.PENDING)
    with django_capture_on_commit_callbacks(execute=True):
        notify("community.request_received", [actor, alice, alice, pending], actor=actor)
    assert list(Notification.objects.values_list("recipient__email", flat=True)) == [
        "alice2@example.com"
    ]


def test_model_target_and_community(make_user, django_capture_on_commit_callbacks):
    from communities.models import Community, CommunityCategory

    category = CommunityCategory.objects.first()
    community = Community.objects.create(name="C", slug="c", tagline="t", category=category)
    bob = make_user("bob@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        notify("community.request_accepted", [bob], target=community, community=community)
    row = Notification.objects.get()
    assert row.target_type == "communities.community"
    assert row.target_id == str(community.public_id)
    assert row.community == community
    assert row.actor is None


def test_nothing_registered_without_recipients(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks() as callbacks:
        notify("community.invited", [])
    assert callbacks == []
