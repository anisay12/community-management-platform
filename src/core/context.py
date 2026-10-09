"""Per-request context (request id, client IP, current user) usable outside views."""

import ipaddress
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any

from django.conf import settings
from django.http import HttpRequest


@dataclass(frozen=True)
class RequestContext:
    request_id: str | None = None
    ip: str | None = None
    user_source: Callable[[], Any] | None = None

    @property
    def user(self) -> Any:
        """The current user, resolved at read time so login/logout mid-request is seen."""
        return self.user_source() if self.user_source else None


_context: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def _valid_ip(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def client_ip(request: HttpRequest) -> str | None:
    """Return the client IP, trusting only the last ``NUM_PROXIES`` proxies' forwarding."""
    remote = request.META.get("REMOTE_ADDR")
    proxies = settings.NUM_PROXIES
    if proxies > 0:
        forwarded = [
            part.strip()
            for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
            if part.strip()
        ]
        if len(forwarded) >= proxies:
            return _valid_ip(forwarded[-proxies])
    return _valid_ip(remote)


def get_request_context() -> RequestContext:
    return _context.get() or RequestContext()


def bind_request(request: HttpRequest) -> None:
    _context.set(
        RequestContext(request_id=getattr(request, "request_id", None), ip=client_ip(request))
    )


def set_user(user: Any) -> None:
    set_user_source(lambda: user)


def set_user_source(source: Callable[[], Any]) -> None:
    _context.set(replace(get_request_context(), user_source=source))


def clear_request() -> None:
    _context.set(RequestContext())
