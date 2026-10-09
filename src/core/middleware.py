import re
import uuid
from collections.abc import Callable

import structlog
from django.http import HttpRequest, HttpResponse

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class RequestIDMiddleware:
    """Associe un identifiant de corrélation à chaque requête, aux logs et à la réponse."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = self.get_response(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response[REQUEST_ID_HEADER] = request_id
        return response
