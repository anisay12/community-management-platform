"""One JSON error shape for the API, and the HTTP status of every ``DomainError`` code.

Body: ``{"code", "detail", "fields"?, "request_id"}``; ``fields`` maps dotted field paths
(``author.email``, ``items.1.title``) to lists of messages. Django's own 400-class
exceptions (``BadRequest``, ``SuspiciousOperation``) stay 400 (code ``bad_request``).
Every 429 carries ``Retry-After``. A new ``DomainError`` code must be
added to one of the sets below: ``tests/test_errors.py`` fails otherwise.
"""

import logging

import structlog
from django.conf import settings
from django.core.exceptions import BadRequest, SuspiciousOperation
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.signals import got_request_exception
from django.http import Http404
from django.utils.log import log_response
from django.utils.translation import gettext as _
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.views import set_rollback

from core.errors import DomainError

logger = structlog.get_logger(__name__)

RATE_LIMITED = "rate_limited"
FORBIDDEN_CODES = frozenset(
    {
        "forbidden",
        "forbidden_role",
        "forbidden_target",
        "invite_only",
        "kind_forbidden",
        "not_member",
        "own_content",
        "read_only",
        "self_action",
        "tag_creation_forbidden",
    }
)
CONFLICT_CODES = frozenset(
    {
        "already_member",
        "already_reported",
        "edit_conflict",
        "email_taken",
        "expired",
        "idempotency_key_reused",
        "invalid_state",
        "last_owner",
        "name_taken",
        "pin_limit",
        "slug_taken",
        "submission_in_progress",
        "version_label_taken",
    }
)
UNAVAILABLE_CODES = frozenset({"idempotency_unavailable", "storage_unavailable"})
BAD_REQUEST_CODES = frozenset(
    {
        "body_required",
        "body_too_long",
        "change_note_too_long",
        "depth_exceeded",
        "description_too_long",
        "extension_refused",
        "file_empty",
        "file_too_large",
        "first_name_required",
        "first_name_too_long",
        "idempotency_key_required",
        "image_too_large",
        "invalid",
        "invalid_choice",
        "invalid_date",
        "invalid_email",
        "invalid_image",
        "invalid_link",
        "invalid_reason",
        "invalid_status",
        "invalid_type",
        "last_name_required",
        "last_name_too_long",
        "manager_requires_unit",
        "manager_self",
        "name_required",
        "name_too_long",
        "reason_required",
        "same_tag",
        "tag_unavailable",
        "title_required",
        "title_too_long",
        "too_many_collections",
        "type_mismatch",
        "unknown_manager",
        "unknown_role",
        "version_label_too_long",
    }
)


def status_for(code: str) -> int:
    """HTTP status of a ``DomainError`` code (400 for any code not listed)."""
    if code == RATE_LIMITED:
        return 429
    if code in FORBIDDEN_CODES:
        return 403
    if code in CONFLICT_CODES:
        return 409
    if code in UNAVAILABLE_CODES:
        return 503
    return 400


def _body(code: str, detail, request, fields=None) -> dict:
    body = {"code": code, "detail": str(detail)}
    if fields is not None:
        body["fields"] = fields
    body["request_id"] = getattr(request, "request_id", None)
    return body


def _flatten(detail, prefix: str, out: dict) -> None:
    if isinstance(detail, dict):
        for name, value in detail.items():
            _flatten(value, f"{prefix}.{name}" if prefix else str(name), out)
    elif isinstance(detail, (list, tuple)):
        if all(not isinstance(item, (dict, list, tuple)) for item in detail):
            out.setdefault(prefix or "non_field_errors", []).extend(str(i) for i in detail)
        else:
            for index, item in enumerate(detail):
                _flatten(item, f"{prefix}.{index}" if prefix else str(index), out)
    else:
        out.setdefault(prefix or "non_field_errors", []).append(str(detail))


def _fields(detail) -> dict:
    out: dict = {}
    _flatten(detail, "", out)
    return out


def exception_handler(exc, context):
    request = getattr(context.get("request"), "_request", None)
    if isinstance(exc, DomainError):
        set_rollback()
        status = status_for(exc.code)
        response = Response(_body(exc.code, exc.message, request), status=status)
        if status == 429:
            response["Retry-After"] = str(int(getattr(exc, "retry_after", 3600)))
        return response
    if isinstance(exc, (BadRequest, SuspiciousOperation)):
        set_rollback()  # Django answers these with a 400: keep it, but as JSON
        response = Response(
            _body("bad_request", _("The request could not be processed."), request), status=400
        )
        if isinstance(exc, SuspiciousOperation):
            # Parity with django.core.handlers.exception.response_for_exception.
            log_response(
                str(exc),
                exception=exc,
                request=request,
                response=response,
                level="error",
                logger=logging.getLogger(f"django.security.{exc.__class__.__name__}"),
            )
        return response
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()  # never echo the message: it may name the model
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()
    if isinstance(exc, exceptions.APIException):
        response = drf_exception_handler(exc, context)  # rollback, WWW-Authenticate, Retry-After
        if isinstance(exc, exceptions.ValidationError):
            response.data = _body(
                "invalid", _("The submitted data is not valid."), request, _fields(exc.detail)
            )
        else:
            codes = exc.get_codes()
            code = codes if isinstance(codes, str) else exc.default_code
            if isinstance(exc, exceptions.Throttled):
                code = RATE_LIMITED
            detail = exc.detail if isinstance(exc.detail, str) else exc.default_detail
            response.data = _body(code, detail, request)
            if isinstance(exc, exceptions.Throttled) and "Retry-After" not in response:
                response["Retry-After"] = "3600"
        return response
    if settings.DEBUG:
        return None  # DRF re-raises: the developer gets Django's traceback page
    set_rollback()
    logger.exception("api.unhandled_error")
    # Let error collectors and Django's request logging see it, as for any page.
    got_request_exception.send(sender=None, request=request)
    return Response(_body("server_error", _("An unexpected error occurred."), request), status=500)
