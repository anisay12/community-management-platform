"""App wiring: Feed tab, navigation entries, error responses, anonymizer."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.test import RequestFactory

from communities.models import Community
from communities.tabs import tabs_for
from core import navigation
from core.errors import DomainError
from posts.models import Comment, Post
from posts.privacy import anonymize_author
from posts.views_errors import domain_error_response

pytestmark = pytest.mark.django_db


def _labels(user, community):
    return [str(label) for label, _url, _active in tabs_for(user, community, "about")]


def test_feed_tab_first_and_for_content_readers_only(
    make_community, active_user, make_user, add_member
):
    open_community = make_community()
    assert _labels(active_user, open_community)[0] == "Feed"
    private = make_community("Private", access_mode=Community.AccessMode.REQUEST)
    assert "Feed" not in _labels(active_user, private)
    member = make_user("member@example.com")
    add_member(private, member)
    assert _labels(member, private)[0] == "Feed"
    assert "Feed" not in _labels(AnonymousUser(), open_community)


def test_navigation_entries_shown_once_routes_exist(rf, active_user):
    request = rf.get("/")
    request.user = active_user
    keys = [item.key for item in navigation.items_for(request)]
    assert keys.index("home_feed") == keys.index("communities") + 1
    assert keys.index("bookmarks") > keys.index("home_feed")


def _request(method="get", **headers):
    request = getattr(RequestFactory(), method)("/", headers=headers)
    request.session = SessionStore()
    request._messages = FallbackStorage(request)
    request.user = AnonymousUser()
    return request


def test_rate_limited_answers_429_with_retry_after():
    error = DomainError("rate_limited", "Slow down")
    error.retry_after = 42
    response = domain_error_response(_request(), error, redirect_to="/back/")
    assert response.status_code == 429
    assert response["Retry-After"] == "42"


def test_edit_conflict_answers_409(active_user):
    request = _request()
    request.user = active_user
    error = DomainError("edit_conflict", "Someone else edited this post.")
    response = domain_error_response(request, error, redirect_to="/post/")
    assert response.status_code == 409
    assert b"Someone else edited this post." in response.content
    assert b'href="/post/"' in response.content


def test_other_errors_toast_and_redirect():
    request = _request()
    response = domain_error_response(request, DomainError("forbidden", "No"), redirect_to="/b/")
    assert (response.status_code, response["Location"]) == (302, "/b/")
    assert [str(m) for m in request._messages] == ["No"]
    htmx = _request(HX_Request="true")
    response = domain_error_response(htmx, DomainError("pin_limit", "Max"), redirect_to="/b/")
    assert (response.status_code, response["HX-Redirect"]) == (204, "/b/")


def test_anonymizer_replaces_author_names(make_community, active_user, make_post, make_comment):
    post = make_post(make_community(), active_user)
    make_comment(post, active_user)
    anonymize_author(active_user)
    assert Post.objects.get().author_display == "Former employee"
    assert Comment.objects.get().author_display == "Former employee"


def test_anonymizer_is_registered():
    from accounts import privacy

    assert anonymize_author in privacy._anonymizers
