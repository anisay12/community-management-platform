import json
import logging.config
import re

import structlog
from django.http import HttpResponse
from django.test import RequestFactory

from core.logging import build_logging
from core.middleware import RequestIDMiddleware


def _run(headers: dict[str, str]) -> tuple[HttpResponse, dict]:
    seen: dict = {}

    def view(request):
        seen.update(structlog.contextvars.get_contextvars())
        seen["attr"] = request.request_id
        return HttpResponse("ok")

    request = RequestFactory().get("/", headers=headers)
    response = RequestIDMiddleware(view)(request)
    return response, seen


def test_valid_incoming_request_id_is_propagated():
    response, seen = _run({"X-Request-ID": "abc12345-def"})
    assert response["X-Request-ID"] == "abc12345-def"
    assert seen["request_id"] == "abc12345-def"
    assert seen["attr"] == "abc12345-def"


def test_invalid_incoming_request_id_is_replaced():
    response, seen = _run({"X-Request-ID": "bad id <script>"})
    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])
    assert seen["request_id"] == response["X-Request-ID"]


def test_missing_request_id_is_generated():
    response, _ = _run({})
    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])


def test_json_log_line_contains_request_id(capsys):
    logging.config.dictConfig(build_logging(level="INFO", json=True))
    structlog.contextvars.bind_contextvars(request_id="req-12345678")
    try:
        structlog.get_logger("core.test").info("hello", answer=42)
    finally:
        structlog.contextvars.clear_contextvars()
    line = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello"
    assert payload["answer"] == 42
    assert payload["request_id"] == "req-12345678"
    assert payload["level"] == "info"
    assert payload["logger"] == "core.test"
    assert "timestamp" in payload
