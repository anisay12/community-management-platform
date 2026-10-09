from django.http import HttpResponse
from django.test import RequestFactory

from core.context import bind_request, clear_request, client_ip, get_request_context, set_user
from core.middleware import RequestIDMiddleware, RequestUserContextMiddleware


def _req(remote="203.0.113.9", xff=None):
    headers = {"X-Forwarded-For": xff} if xff is not None else {}
    return RequestFactory().get("/", REMOTE_ADDR=remote, headers=headers)


def test_client_ip_ignores_forwarded_header_without_proxies(settings):
    settings.NUM_PROXIES = 0
    assert client_ip(_req(xff="1.1.1.1")) == "203.0.113.9"


def test_client_ip_takes_nth_from_right(settings):
    settings.NUM_PROXIES = 1
    assert client_ip(_req(xff="6.6.6.6, 198.51.100.7")) == "198.51.100.7"
    settings.NUM_PROXIES = 2
    assert client_ip(_req(xff="6.6.6.6, 198.51.100.7, 10.0.0.1")) == "198.51.100.7"


def test_client_ip_falls_back_to_remote_addr_when_header_short_or_missing(settings):
    settings.NUM_PROXIES = 2
    assert client_ip(_req(xff="198.51.100.7")) == "203.0.113.9"
    assert client_ip(_req()) == "203.0.113.9"


def test_client_ip_returns_none_when_invalid(settings):
    settings.NUM_PROXIES = 1
    assert client_ip(_req(xff="garbage")) is None
    settings.NUM_PROXIES = 0
    assert client_ip(_req(remote="not-an-ip")) is None


def test_bind_and_clear_request_context(settings):
    settings.NUM_PROXIES = 0
    request = _req()
    request.request_id = "abc12345"
    bind_request(request)
    ctx = get_request_context()
    assert (ctx.request_id, ctx.ip, ctx.user) == ("abc12345", "203.0.113.9", None)
    sentinel = object()
    set_user(sentinel)
    assert get_request_context().user is sentinel
    clear_request()
    ctx = get_request_context()
    assert (ctx.request_id, ctx.ip, ctx.user) == (None, None, None)


def test_middlewares_populate_context_during_request_and_clear_it_after(settings):
    settings.NUM_PROXIES = 0
    seen = {}

    def view(request):
        seen["ctx"] = get_request_context()
        return HttpResponse("ok")

    request = _req()
    request.user = "the-user"
    handler = RequestIDMiddleware(RequestUserContextMiddleware(view))
    handler(request)
    ctx = seen["ctx"]
    assert ctx.request_id == request.request_id
    assert ctx.ip == "203.0.113.9"
    assert ctx.user == "the-user"
    assert get_request_context().request_id is None
    assert get_request_context().user is None


def test_user_is_resolved_lazily_from_the_request(settings):
    settings.NUM_PROXIES = 0
    seen = {}

    def view(request):
        request.user = "logged-in-later"
        seen["user"] = get_request_context().user
        return HttpResponse("ok")

    request = _req()
    request.user = "anonymous"
    RequestIDMiddleware(RequestUserContextMiddleware(view))(request)
    assert seen["user"] == "logged-in-later"
