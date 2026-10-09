from datetime import timedelta
from smtplib import SMTPException
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.contrib.auth import get_user
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.models import Session
from django.db import connection
from django.http import HttpResponse
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import User, UserSession
from accounts.services import end_all_sessions
from accounts.tasks import send_email
from conftest import PASSWORD
from core.middleware import ActivityMiddleware

pytestmark = pytest.mark.django_db


def login(client, email="alice@example.com"):
    response = client.post(reverse("accounts:login"), {"username": email, "password": PASSWORD})
    assert response.status_code == 302
    return client.session.session_key


def test_login_records_session_and_logout_removes_it(client, active_user):
    key = login(client)
    assert list(active_user.tracked_sessions.values_list("session_key", flat=True)) == [key]
    client.post(reverse("accounts:logout"))
    assert not UserSession.objects.exists()


def test_end_all_sessions_logs_out_every_device(active_user):
    first, second = Client(), Client()
    login(first)
    login(second)
    assert active_user.tracked_sessions.count() == 2
    assert end_all_sessions(active_user) == 2
    assert not UserSession.objects.exists()
    for client in (first, second):
        response = client.get(reverse("home"))
        assert not response.wsgi_request.user.is_authenticated


def test_suspending_user_invalidates_existing_session(client, active_user):
    login(client)
    active_user.status = User.Status.SUSPENDED
    active_user.save()
    end_all_sessions(active_user)
    response = client.get(reverse("home"))
    assert not get_user(response.wsgi_request).is_authenticated


def test_suspended_status_alone_makes_session_anonymous(client, active_user):
    client.force_login(active_user)
    User.objects.filter(pk=active_user.pk).update(status=User.Status.SUSPENDED)
    response = client.get(reverse("home"))
    assert not response.wsgi_request.user.is_authenticated


def _user_updates(queries):
    return [
        q["sql"]
        for q in queries
        if q["sql"].startswith("UPDATE") and '"accounts_user"' in q["sql"].split("SET")[0]
    ]


def test_last_seen_updated_at_most_once_per_five_minutes(client, active_user):
    client.force_login(active_user)
    with CaptureQueriesContext(connection) as ctx:
        client.get(reverse("home"))
        client.get(reverse("home"))
    assert len(_user_updates(ctx.captured_queries)) == 1
    active_user.refresh_from_db()
    assert active_user.last_seen_at is not None


def test_last_seen_refreshed_after_five_minutes(client, active_user):
    stale = timezone.now() - timedelta(minutes=6)
    User.objects.filter(pk=active_user.pk).update(last_seen_at=stale)
    client.force_login(active_user)
    client.get(reverse("home"))
    active_user.refresh_from_db()
    assert active_user.last_seen_at > stale


def _expire_date(key):
    return Session.objects.get(pk=key).expire_date


def test_activity_refreshes_session_expiry(client, active_user):
    key = login(client)
    # Pretend the stored session is close to expiry, then make a request after the throttle.
    soon = timezone.now() + timedelta(hours=1)
    Session.objects.filter(pk=key).update(expire_date=soon)
    User.objects.filter(pk=active_user.pk).update(
        last_seen_at=timezone.now() - timedelta(minutes=10)
    )
    client.get(reverse("home"))
    assert _expire_date(key) > soon + timedelta(hours=6)


def test_session_expiry_not_refreshed_within_throttle_window(client, active_user):
    key = login(client)
    soon = timezone.now() + timedelta(hours=1)
    Session.objects.filter(pk=key).update(expire_date=soon)
    User.objects.filter(pk=active_user.pk).update(
        last_seen_at=timezone.now() - timedelta(minutes=1)
    )
    client.get(reverse("home"))
    assert _expire_date(key) == soon


def test_rotated_session_key_is_tracked_and_ended(client, active_user):
    old_key = login(client)
    session = client.session
    session.cycle_key()
    session.save()
    new_key = session.session_key
    assert new_key != old_key
    client.cookies[settings.SESSION_COOKIE_NAME] = new_key
    User.objects.filter(pk=active_user.pk).update(
        last_seen_at=timezone.now() - timedelta(minutes=10)
    )
    client.get(reverse("home"))
    assert UserSession.objects.filter(session_key=new_key, user=active_user).exists()
    assert end_all_sessions(active_user) >= 1
    assert not Session.objects.filter(pk=new_key).exists()
    assert not client.get(reverse("home")).wsgi_request.user.is_authenticated


def test_anonymous_request_does_not_touch_users(client):
    with CaptureQueriesContext(connection) as ctx:
        client.get(reverse("home"))
    assert _user_updates(ctx.captured_queries) == []


def test_timezone_active_during_view(rf, active_user):
    active_user.profile.timezone = "Asia/Tokyo"
    active_user.profile.save()
    captured = {}

    def view(request):
        captured["tz"] = timezone.get_current_timezone()
        return HttpResponse()

    request = rf.get("/")
    request.user = active_user
    request.session = Client().session
    ActivityMiddleware(view)(request)
    assert captured["tz"] == ZoneInfo("Asia/Tokyo")
    assert timezone.get_current_timezone_name() == "Europe/Paris"

    request = rf.get("/")
    request.user = AnonymousUser()
    ActivityMiddleware(view)(request)
    assert str(captured["tz"]) == "Europe/Paris"


def test_send_email_task_retry_policy():
    assert send_email.autoretry_for == (SMTPException, OSError)
    assert send_email.retry_backoff is True
    assert send_email.max_retries == 5
