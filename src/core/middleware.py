import re
import uuid
from collections.abc import Callable

import structlog
from django.http import HttpRequest, HttpResponse

from .context import bind_request, clear_request, set_user

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class RequestIDMiddleware:
    """Attach a correlation identifier to each request, to the logs and to the response."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        bind_request(request)
        try:
            response = self.get_response(request)
        finally:
            structlog.contextvars.clear_contextvars()
            clear_request()
        response[REQUEST_ID_HEADER] = request_id
        return response


class RequestUserContextMiddleware:
    """Expose the authenticated user to code that has no request (signals, services)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        set_user(getattr(request, "user", None))
        return self.get_response(request)
