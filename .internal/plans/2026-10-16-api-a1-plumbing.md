# REST API foundation — A1 plumbing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use beads-superpowers:subagent-driven-development (recommended) or beads-superpowers:executing-plans to implement this plan task-by-task. Each Task becomes a bead (`bd create -t task --parent <epic-id>`). Steps within tasks use checkbox (`- [ ]`) syntax for human readability.

**Goal:** Add Django REST Framework to the project with the shared API plumbing (session-only
auth with a 401 for anonymous callers, an active + MFA-verified permission, a uniform error
body, pagination, throttles, idempotent writes, `/api/v1/me/`, `/api/v1/tags/`, and the OpenAPI
schema and docs) so that slices A2–A4 only add domain endpoints.

**Architecture:** A new `src/api/` app holds plumbing only and imports no domain internals
beyond the public `policies`, `services` and models it needs (`accounts.policies.requires_mfa`,
`taxonomy`). DRF views run behind the existing middleware stack. The MFA middleware lets DRF
views under `/api/` through, and the default permission enforces MFA there with JSON answers
instead of redirects.

**Tech Stack:** Django 5.2, djangorestframework ≥ 3.16, drf-spectacular[sidecar] ≥ 0.28,
pytest + pytest-django, uv.

**Spec:** `.internal/specs/2026-10-16-api-foundation-design.md` (approved, stress-tested).

## Global Constraints

- Session authentication only; **no endpoint under `/api/` accepts a password, an OTP or a token** (spec § 5).
- No CORS package or middleware; no `Access-Control-Allow-Origin` header ever sent.
- Hidden object → 404; forbidden action on a visible object → 403 (AGENTS.md § 4).
- Error body: `{"code", "detail", "fields"?, "request_id"}`; `detail` translated (EN default, FR complete).
- Throttle rates: `api_read` `3600/hour`, `api_write` `1000/hour`, overridable by the env vars `API_THROTTLE_READ` / `API_THROTTLE_WRITE`; `api_write` ≥ the sum of `POSTS_RATE_LIMITS`.
- Pagination: numbered, size 20, max 50 (`?page_size=`).
- `Idempotency-Key` must be a UUID; scope strings are shared with the UI views.
- `/api/docs/` uses `SpectacularSwaggerSplitView` with sidecar assets (no CDN, no inline script).
- Every user-facing string goes through gettext and gets a French translation; `make i18n-check` stays green.
- Coverage ≥ 85 %; never skip, xfail or weaken a test.
- Git: conservative profile. Commit per task on the branch `api/a1-plumbing`. Do not push without the user's approval.

## File structure

| Path | Responsibility |
|---|---|
| `pyproject.toml`, `uv.lock` | Add `djangorestframework`, `drf-spectacular[sidecar]` |
| `src/config/settings/base.py` | `INSTALLED_APPS`, `REST_FRAMEWORK`, `SPECTACULAR_SETTINGS`, throttle env vars |
| `src/config/settings/dev.py` | Browsable renderer in dev only |
| `src/config/urls.py` | `path("api/", include("api.urls"))` |
| `src/api/__init__.py`, `apps.py` | App config |
| `src/api/authentication.py` | `SessionAuthentication` subclass that answers 401 |
| `src/api/permissions.py` | `IsActiveVerifiedUser` |
| `src/api/exceptions.py` | `status_for`, `exception_handler`, the code sets |
| `src/api/pagination.py` | `PagePagination` |
| `src/api/throttling.py` | `ReadThrottle`, `WriteThrottle` |
| `src/api/idempotency.py` | `Outcome`, `fingerprint`, `run_idempotent`, `mark_replayed` |
| `src/api/serializers.py` | `UserSummarySerializer`, `MeSerializer`, `TagSerializer` |
| `src/api/views.py` | `MeView`, `TagListView` |
| `src/api/urls.py` | `/api/v1/…`, `/api/schema/`, `/api/docs/` |
| `src/accounts/middleware.py` | DRF-view exemption |
| `src/api/tests/…` | Tests and test-only URLconfs |
| `Makefile` | Schema validation in `lint` |
| `src/locale/fr/LC_MESSAGES/django.po` | French strings |
| `docs/development.md` | Short "REST API" section |

---

### Task 1: The API front door (dependencies, auth, permission, MFA exemption, `/me/`)

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add`)
- Modify: `src/config/settings/base.py` (`INSTALLED_APPS`, new `REST_FRAMEWORK` block after the Celery settings)
- Modify: `src/config/settings/dev.py`
- Modify: `src/config/urls.py`
- Modify: `src/accounts/middleware.py`
- Create: `src/api/__init__.py`, `src/api/apps.py`, `src/api/authentication.py`, `src/api/permissions.py`, `src/api/serializers.py`, `src/api/views.py`, `src/api/urls.py`
- Create: `src/api/tests/__init__.py`, `src/api/tests/conftest.py`, `src/api/tests/urls_plain.py`, `src/api/tests/test_access.py`

**Interfaces:**
- Produces: `api.authentication.SessionAuthentication`; `api.permissions.IsActiveVerifiedUser`
  (raises `NotAuthenticated`, or `PermissionDenied` with code `mfa_required`; inactive accounts
  are anonymous to DRF and so get 401);
  `api.serializers.UserSummarySerializer` (fields `public_id`, `display_name`, `avatar_url`);
  URL name `api:me` → `/api/v1/me/`; the fixture `api_client`.

**Acceptance Criteria:**
- Anonymous `GET /api/v1/me/` → 401 with a `WWW-Authenticate` header.
- Active employee → 200 with their `public_id`, `display_name`, `roles`, `requires_mfa`.
- Suspended or pending user with a session → 401; functional admin without a verified second factor → 403 `mfa_required` (not a 302 redirect); with a verified factor → 200.
- A plain Django view under `/api/` still gets the MFA redirect (302) for an unverified admin.
- `DEFAULT_AUTHENTICATION_CLASSES == ["api.authentication.SessionAuthentication"]`.

- [ ] **Step 1: Add the dependencies**

Run: `uv add "djangorestframework>=3.16" "drf-spectacular[sidecar]>=0.28"`
Expected: `pyproject.toml` lists both and `uv.lock` is updated.

- [ ] **Step 2: Write the failing tests**

`src/api/tests/__init__.py`: empty.

`src/api/tests/conftest.py`:

```python
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient


@pytest.fixture(autouse=True)
def _clear_cache():
    """Throttle counters and idempotency keys live in the cache: isolate every test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client():
    return APIClient()
```

`src/api/tests/urls_plain.py` (a plain, non-DRF view under `/api/` for the MFA regression test):

```python
from django.http import HttpResponse
from django.urls import include, path


def plain_view(request):
    return HttpResponse("plain")


urlpatterns = [
    path("api/plain/", plain_view, name="plain"),
    path("", include("config.urls")),
]
```

`src/api/tests/test_access.py`:

```python
import pytest
from django.conf import settings
from django.contrib.auth.models import Group
from django.urls import reverse

from accounts.models import User
from accounts.roles import Role

pytestmark = pytest.mark.django_db

ME = "/api/v1/me/"


def test_me_url_name():
    assert reverse("api:me") == ME


def test_anonymous_gets_401_with_authenticate_header(api_client):
    response = api_client.get(ME)
    assert response.status_code == 401
    assert response.has_header("WWW-Authenticate")


def test_active_employee_reads_me(api_client, active_user):
    api_client.force_login(active_user)
    response = api_client.get(ME)
    assert response.status_code == 200
    body = response.json()
    assert body["public_id"] == str(active_user.public_id)
    assert body["display_name"] == "Alice Doe"
    assert body["requires_mfa"] is False
    assert isinstance(body["roles"], list)


@pytest.mark.parametrize("status", [User.Status.SUSPENDED, User.Status.PENDING])
def test_inactive_account_is_unauthenticated(api_client, make_user, status):
    user = make_user("inactive@example.com", status=status)
    api_client.force_login(user)
    assert api_client.get(ME).status_code == 401


def test_privileged_user_without_mfa_gets_403_not_redirect(api_client, functional_admin):
    api_client.force_login(functional_admin)
    response = api_client.get(ME)
    assert response.status_code == 403
    assert not response.has_header("Location")


def test_privileged_user_with_verified_mfa_reads_me(client, functional_admin, verified_login):
    verified_login(client, functional_admin)
    response = client.get(ME)
    assert response.status_code == 200
    assert response.json()["requires_mfa"] is True


@pytest.mark.urls("api.tests.urls_plain")
def test_plain_django_view_under_api_keeps_mfa_redirect(client, functional_admin):
    client.force_login(functional_admin)
    response = client.get("/api/plain/")
    assert response.status_code == 302
    assert "/mfa/" in response["Location"]


def test_ui_pages_keep_mfa_redirect(client, functional_admin):
    client.force_login(functional_admin)
    assert client.get("/").status_code == 302


def test_session_is_the_only_authentication():
    assert settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] == [
        "api.authentication.SessionAuthentication"
    ]


def test_roles_are_listed(api_client, make_user):
    user = make_user("creator@example.com")
    user.groups.add(Group.objects.get(name=Role.COMMUNITY_CREATOR))
    api_client.force_login(user)
    assert str(Role.COMMUNITY_CREATOR) in api_client.get(ME).json()["roles"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest src/api -q`
Expected: errors (`No module named 'api'` or `NoReverseMatch`).

- [ ] **Step 4: Create the app and the authentication and permission classes**

`src/api/__init__.py`: empty.

`src/api/apps.py`:

```python
from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ApiConfig(AppConfig):
    name = "api"
    verbose_name = _("API")
```

`src/api/authentication.py`:

```python
"""Session authentication for the REST API (spec § 15: same origin, no other scheme)."""

from rest_framework import authentication


class SessionAuthentication(authentication.SessionAuthentication):
    """DRF session authentication that answers 401 (not 403) to anonymous callers.

    DRF turns ``NotAuthenticated`` into a 403 when the first authentication class has no
    ``authenticate_header``. This scheme name only lets it answer 401. The API never accepts
    credentials: logging in stays in the UI (axes, MFA, SSO modes).
    """

    def authenticate_header(self, request):
        return 'Session realm="api"'
```

`src/api/permissions.py`:

```python
"""Default permission of every API view: an active account with its second factor verified."""

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.permissions import BasePermission

from accounts.policies import requires_mfa


class IsActiveVerifiedUser(BasePermission):
    """Authenticated (hence ``status == active``) and, for privileged roles, MFA verified.

    DRF's session authentication already treats an inactive account (pending, suspended,
    deactivated) as anonymous, so such a session answers 401. This class adds what
    ``accounts.middleware.MFARequiredMiddleware`` enforces for pages, answering JSON
    instead of redirecting.
    """

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not getattr(user, "is_authenticated", False):
            raise NotAuthenticated()
        if requires_mfa(user) and not user.is_verified():
            raise PermissionDenied(
                detail=_("Verify your second factor in the application first."),
                code="mfa_required",
            )
        return True
```

- [ ] **Step 5: Add the serializers, the `/me/` view and the URLs**

`src/api/serializers.py`:

```python
"""Serializers shared by every app's API."""

from rest_framework import serializers

from accounts.policies import requires_mfa
from accounts.roles import user_roles
from accounts.templatetags.accounts_tags import avatar_url


class UserSummarySerializer(serializers.Serializer):
    """How another user appears in the API: the name and photo every employee sees in the UI.

    Bio and interests are not part of it (they follow ``profile_visibility``). Lists must
    ``select_related("profile")``.
    """

    public_id = serializers.UUIDField(read_only=True)
    display_name = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()

    def get_display_name(self, user) -> str:
        return user.get_full_name() or user.email

    def get_avatar_url(self, user) -> str:
        return avatar_url(user)


class MeSerializer(UserSummarySerializer):
    email = serializers.EmailField(read_only=True)
    roles = serializers.SerializerMethodField()
    requires_mfa = serializers.SerializerMethodField()
    language = serializers.SerializerMethodField()
    timezone = serializers.SerializerMethodField()

    def get_roles(self, user) -> list[str]:
        return sorted(user_roles(user))

    def get_requires_mfa(self, user) -> bool:
        return requires_mfa(user)

    def get_language(self, user) -> str:
        profile = getattr(user, "profile", None)
        return profile.language if profile is not None else ""

    def get_timezone(self, user) -> str:
        profile = getattr(user, "profile", None)
        return profile.timezone if profile is not None else ""
```

`src/api/views.py`:

```python
"""Plumbing endpoints of the API: who am I, tag suggestions."""

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import MeSerializer


class MeView(APIView):
    """The signed-in user: identity, global roles, MFA requirement, preferences."""

    @extend_schema(responses=MeSerializer)
    def get(self, request):
        return Response(MeSerializer(request.user, context={"request": request}).data)
```

`src/api/urls.py`:

```python
from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("v1/me/", views.MeView.as_view(), name="me"),
]
```

In `src/config/urls.py`, add after the `documents/` line:

```python
    path("api/", include("api.urls")),
```

- [ ] **Step 6: Add the settings**

In `src/config/settings/base.py`, append to `INSTALLED_APPS` after `"mozilla_django_oidc",`:

```python
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
```

and after `"documents",`:

```python
    "api",
```

Add this block after the `DOCUMENT_*` / ClamAV settings:

```python
# REST API (spec § 15, .internal/specs/2026-10-16-api-foundation-design.md): session only,
# same origin, no CORS. Never add another authentication class without an ADR.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["api.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["api.permissions.IsActiveVerifiedUser"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
}
```

In `src/config/settings/dev.py`, append:

```python
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}
```

- [ ] **Step 7: Exempt DRF views under `/api/` from the MFA redirect**

In `src/accounts/middleware.py`, add the import `from rest_framework.views import APIView`
and this helper above the class:

```python
def _is_api_view(request: HttpRequest, view_func) -> bool:
    """A DRF view under ``/api/``: its default permission enforces MFA with a JSON 403.

    A plain Django view mounted under ``/api/`` is not exempted (it keeps the redirect).
    """
    if not request.path_info.startswith("/api/"):
        return False
    view_class = getattr(view_func, "cls", None)
    return isinstance(view_class, type) and issubclass(view_class, APIView)
```

In `process_view`, right after the `if not requires_mfa(user): return None` lines, add:

```python
        if _is_api_view(request, view_func):
            return None
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest src/api src/accounts -q`
Expected: all pass (`accounts:mfa_setup` is `/mfa/setup/`, so the Location contains `/mfa/`).

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock src/config src/api src/accounts/middleware.py
git commit -m "feat(api): DRF front door: session auth, active+MFA permission, /api/v1/me/"
```

---

### Task 2: Uniform errors (exception handler and the DomainError status map)

**Files:**
- Create: `src/api/exceptions.py`
- Modify: `src/config/settings/base.py` (`"EXCEPTION_HANDLER"` in `REST_FRAMEWORK`)
- Create: `src/api/tests/urls_errors.py`, `src/api/tests/test_errors.py`

**Interfaces:**
- Consumes: `core.errors.DomainError(code, message)` with an optional `retry_after` attribute.
- Produces: `api.exceptions.status_for(code: str) -> int`; `api.exceptions.exception_handler(exc, context) -> Response | None`; the frozensets `FORBIDDEN_CODES`, `CONFLICT_CODES`, `BAD_REQUEST_CODES`, `UNAVAILABLE_CODES` and `RATE_LIMITED = "rate_limited"`.

**Acceptance Criteria:**
- `DomainError` → 400 / 403 / 409 / 429 (with `Retry-After`) / 503 according to the spec § 4 table.
- `Http404` → 404 `not_found` with the same body whatever the message; Django `PermissionDenied` → 403 `forbidden`.
- `ValidationError({"title": [...]})` → 400 `invalid` with `fields.title`.
- Unhandled exception → 500 `server_error` with `request_id` and no traceback, and `got_request_exception` fires; with `DEBUG=True` the exception propagates (debug page).
- Every error body carries `code`, `detail` and `request_id`.
- A test fails if a `DomainError("<code>")` string literal anywhere in `src/` (outside tests) is not classified.

- [ ] **Step 1: Write the test URLconf**

`src/api/tests/urls_errors.py`:

```python
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.urls import include, path
from rest_framework import serializers
from rest_framework.views import APIView

from core.errors import DomainError


def _raising(exc_factory):
    class _View(APIView):
        def get(self, request):
            raise exc_factory()

    return _View.as_view()


def _rate_limited():
    error = DomainError("rate_limited", "Too often")
    error.retry_after = 120
    return error


urlpatterns = [
    path("api/t/conflict/", _raising(lambda: DomainError("edit_conflict", "Changed"))),
    path("api/t/forbidden/", _raising(lambda: DomainError("read_only", "Read only"))),
    path("api/t/bad/", _raising(lambda: DomainError("title_required", "Title"))),
    path("api/t/unknown/", _raising(lambda: DomainError("never_seen_code", "Huh"))),
    path("api/t/storage/", _raising(lambda: DomainError("storage_unavailable", "Down"))),
    path("api/t/rate/", _raising(_rate_limited)),
    path("api/t/404/", _raising(lambda: Http404("No Post matches the given query."))),
    path("api/t/403/", _raising(PermissionDenied)),
    path(
        "api/t/invalid/",
        _raising(lambda: serializers.ValidationError({"title": ["This field is required."]})),
    ),
    path("api/t/boom/", _raising(lambda: RuntimeError("secret internals"))),
    path("", include("config.urls")),
]
```

- [ ] **Step 2: Write the failing tests**

`src/api/tests/test_errors.py`:

```python
import ast
import pathlib

import pytest

from api import exceptions

pytestmark = [pytest.mark.django_db, pytest.mark.urls("api.tests.urls_errors")]

SRC = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def logged(api_client, active_user):
    api_client.force_login(active_user)
    return api_client


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/api/t/conflict/", 409, "edit_conflict"),
        ("/api/t/forbidden/", 403, "read_only"),
        ("/api/t/bad/", 400, "title_required"),
        ("/api/t/unknown/", 400, "never_seen_code"),
        ("/api/t/storage/", 503, "storage_unavailable"),
        ("/api/t/rate/", 429, "rate_limited"),
        ("/api/t/404/", 404, "not_found"),
        ("/api/t/403/", 403, "permission_denied"),
        ("/api/t/invalid/", 400, "invalid"),
        ("/api/t/boom/", 500, "server_error"),
    ],
)
def test_error_shape_and_status(logged, path, status, code):
    response = logged.get(path)
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    assert body["detail"]
    assert body["request_id"] == response["X-Request-ID"]


def test_permission_error_codes(api_client, make_user, functional_admin):
    assert api_client.get("/api/v1/me/").json()["code"] == "not_authenticated"
    suspended = make_user("sus@example.com", status="suspended")
    api_client.force_login(suspended)
    assert api_client.get("/api/v1/me/").json()["code"] == "not_authenticated"
    api_client.force_login(functional_admin)
    assert api_client.get("/api/v1/me/").json()["code"] == "mfa_required"


def test_rate_limited_sets_retry_after(logged):
    assert logged.get("/api/t/rate/")["Retry-After"] == "120"


def test_not_found_does_not_echo_the_message(logged):
    assert "Post" not in logged.get("/api/t/404/").json()["detail"]


def test_validation_errors_are_listed_per_field(logged):
    assert logged.get("/api/t/invalid/").json()["fields"] == {
        "title": ["This field is required."]
    }


def test_server_error_hides_internals(logged):
    assert "secret internals" not in logged.get("/api/t/boom/").content.decode()


def test_server_error_reaches_error_collectors(logged):
    from django.core.signals import got_request_exception

    received = []

    def receiver(sender, request=None, **kwargs):
        received.append(request)

    got_request_exception.connect(receiver)
    try:
        logged.get("/api/t/boom/")
    finally:
        got_request_exception.disconnect(receiver)
    assert received


def test_server_error_reraises_in_debug(logged, settings):
    settings.DEBUG = True
    with pytest.raises(RuntimeError):
        logged.get("/api/t/boom/")


def test_error_detail_is_translated(logged, active_user):
    active_user.profile.language = "fr"
    active_user.profile.save()
    response = logged.get("/api/t/404/", HTTP_ACCEPT_LANGUAGE="fr")
    assert response.json()["detail"] != "Not found."


def _domain_error_codes() -> set[str]:
    codes = set()
    for file in SRC.rglob("*.py"):
        if "tests" in file.parts or "migrations" in file.parts:
            continue
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", None))
            first = node.args[0]
            if name == "DomainError" and isinstance(first, ast.Constant):
                codes.add(first.value)
    return codes


def test_every_domain_error_code_is_classified():
    classified = (
        exceptions.FORBIDDEN_CODES
        | exceptions.CONFLICT_CODES
        | exceptions.BAD_REQUEST_CODES
        | exceptions.UNAVAILABLE_CODES
        | {exceptions.RATE_LIMITED}
    )
    missing = _domain_error_codes() - classified
    assert not missing, f"classify these DomainError codes in api/exceptions.py: {missing}"
```

Note: the 403 code for Django's `PermissionDenied` is DRF's `permission_denied`. The spec's
`forbidden` refers to the `DomainError` code. Keep DRF's code for the generic case.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest src/api/tests/test_errors.py -q`
Expected: FAIL (`ModuleNotFoundError: api.exceptions`, or DRF's default `{"detail": …}` body).

- [ ] **Step 4: Implement the handler**

`src/api/exceptions.py`:

```python
"""One JSON error shape for the API, and the HTTP status of every ``DomainError`` code.

Body: ``{"code", "detail", "fields"?, "request_id"}``. A new ``DomainError`` code must be
added to one of the sets below: ``tests/test_errors.py`` fails otherwise.
"""

import structlog
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
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
        "idempotency_key_required",
        "image_too_large",
        "invalid",
        "invalid_choice",
        "invalid_date",
        "invalid_image",
        "invalid_link",
        "invalid_reason",
        "invalid_status",
        "invalid_type",
        "name_required",
        "name_too_long",
        "reason_required",
        "same_tag",
        "tag_unavailable",
        "title_required",
        "title_too_long",
        "too_many_collections",
        "type_mismatch",
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


def _fields(detail) -> dict:
    if isinstance(detail, dict):
        return {
            name: [str(item) for item in (errors if isinstance(errors, list) else [errors])]
            for name, errors in detail.items()
        }
    return {"non_field_errors": [str(item) for item in detail]}


def exception_handler(exc, context):
    request = getattr(context.get("request"), "_request", None)
    if isinstance(exc, DomainError):
        set_rollback()
        status = status_for(exc.code)
        response = Response(_body(exc.code, exc.message, request), status=status)
        if status == 429:
            response["Retry-After"] = str(getattr(exc, "retry_after", 3600))
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
        return response
    if settings.DEBUG:
        return None  # DRF re-raises: the developer gets Django's traceback page
    set_rollback()
    logger.exception("api.unhandled_error")
    # Let error collectors and Django's request logging see it, as for any page.
    got_request_exception.send(sender=None, request=request)
    return Response(_body("server_error", _("An unexpected error occurred."), request), status=500)
```

Add the imports `from django.conf import settings` and
`from django.core.signals import got_request_exception`.

In `REST_FRAMEWORK` (base.py), add:

```python
    "EXCEPTION_HANDLER": "api.exceptions.exception_handler",
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest src/api -q`
Expected: PASS. If the translation test fails because `Accept-Language` loses to the saved
profile language, keep the profile set to `fr` (as above). `UserPreferencesMiddleware` applies
it.

- [ ] **Step 6: Commit**

```bash
git add src/api src/config/settings/base.py
git commit -m "feat(api): uniform JSON errors and DomainError status map"
```

---

### Task 3: Pagination and throttles

**Files:**
- Create: `src/api/pagination.py`, `src/api/throttling.py`
- Modify: `src/config/settings/base.py`
- Create: `src/api/tests/urls_plumbing.py`, `src/api/tests/test_pagination_throttling.py`

**Interfaces:**
- Produces: `api.pagination.PagePagination` (page size 20, max 50, `?page=`, `?page_size=`); `api.throttling.ReadThrottle` (scope `api_read`, safe methods only) and `WriteThrottle` (scope `api_write`, unsafe methods only).
- Note: `FeedCursorPagination` (spec § 2) is built in A3 next to its only consumers, the feed endpoints, because it wraps `posts.selectors.FeedPage`.

**Acceptance Criteria:**
- A list of 120 items returns 20 by default, `next` set and `count == 120`; `?page_size=100` returns 50.
- With `api_write` lowered to `2/hour`, the third POST answers 429 `rate_limited` with `Retry-After`; GETs are not counted by the write throttle.
- `api_write` ≥ `sum(POSTS_RATE_LIMITS.values())`.
- With the cache raising on every call, GET and POST still succeed (throttles fail open and log `api.throttle.unavailable`).

- [ ] **Step 1: Write the failing tests**

`src/api/tests/urls_plumbing.py`:

```python
from django.urls import include, path
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import PagePagination


class NumbersView(ListAPIView):
    pagination_class = PagePagination

    def get_queryset(self):
        return list(range(120))

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(page)


class EchoView(APIView):
    def get(self, request):
        return Response({"ok": True})

    def post(self, request):
        return Response({"ok": True}, status=201)


urlpatterns = [
    path("api/t/numbers/", NumbersView.as_view()),
    path("api/t/echo/", EchoView.as_view()),
    path("", include("config.urls")),
]
```

`src/api/tests/test_pagination_throttling.py`:

```python
import pytest
from django.conf import settings

from api.throttling import WriteThrottle

pytestmark = [pytest.mark.django_db, pytest.mark.urls("api.tests.urls_plumbing")]


@pytest.fixture
def logged(api_client, active_user):
    api_client.force_login(active_user)
    return api_client


def test_default_page(logged):
    body = logged.get("/api/t/numbers/").json()
    assert body["count"] == 120
    assert len(body["results"]) == 20
    assert body["next"]


def test_page_size_is_capped(logged):
    assert len(logged.get("/api/t/numbers/?page_size=100").json()["results"]) == 50
    assert len(logged.get("/api/t/numbers/?page_size=5").json()["results"]) == 5


def test_write_throttle(logged, monkeypatch):
    monkeypatch.setattr(WriteThrottle, "THROTTLE_RATES", {"api_write": "2/hour"})
    assert logged.post("/api/t/echo/").status_code == 201
    assert logged.post("/api/t/echo/").status_code == 201
    for _ in range(5):
        assert logged.get("/api/t/echo/").status_code == 200
    response = logged.post("/api/t/echo/")
    assert response.status_code == 429
    assert response.json()["code"] == "rate_limited"
    assert response.has_header("Retry-After")


def test_throttles_fail_open_when_cache_is_down(logged, monkeypatch):
    from rest_framework.throttling import SimpleRateThrottle

    def broken(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(SimpleRateThrottle.cache, "get", broken)
    assert logged.get("/api/t/echo/").status_code == 200
    assert logged.post("/api/t/echo/").status_code == 201


def test_write_rate_covers_business_limits():
    count, _period = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["api_write"].split("/")
    assert int(count) >= sum(settings.POSTS_RATE_LIMITS.values())
```

Also add:

```python
def test_max_page_size_constant():
    from api.pagination import PagePagination

    assert PagePagination.max_page_size == 50
    assert PagePagination.page_size == 20
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest src/api/tests/test_pagination_throttling.py -q`
Expected: FAIL (`ModuleNotFoundError: api.pagination`).

- [ ] **Step 3: Implement**

`src/api/pagination.py`:

```python
"""Numbered pagination for catalogues and lists (spec § 2: size 20, max 50)."""

from rest_framework.pagination import PageNumberPagination


class PagePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50
```

`src/api/throttling.py`:

```python
"""Coarse per-user anti-abuse limits for the API.

The precise per-action limits (posts, comments, reactions per hour) stay in the services
(``posts.ratelimit``) and are shared with the UI; these sit well above them.
"""

import structlog
from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import UserRateThrottle

logger = structlog.get_logger(__name__)


class _FailOpenThrottle(UserRateThrottle):
    """When the cache (Redis) is down, allow the request and log it, like posts.ratelimit
    (spec § 16: Redis unavailable → degraded, not down)."""

    applies_to_safe_methods: bool

    def allow_request(self, request, view) -> bool:
        if (request.method in SAFE_METHODS) != self.applies_to_safe_methods:
            return True
        try:
            return super().allow_request(request, view)
        except Exception:  # cache backend down: never block the API on it
            logger.warning("api.throttle.unavailable", scope=self.scope, exc_info=True)
            return True


class ReadThrottle(_FailOpenThrottle):
    scope = "api_read"
    applies_to_safe_methods = True


class WriteThrottle(_FailOpenThrottle):
    scope = "api_write"
    applies_to_safe_methods = False
```

In `REST_FRAMEWORK` (base.py), add:

```python
    "DEFAULT_PAGINATION_CLASS": "api.pagination.PagePagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": ["api.throttling.ReadThrottle", "api.throttling.WriteThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "api_read": env("API_THROTTLE_READ", default="3600/hour"),
        "api_write": env("API_THROTTLE_WRITE", default="1000/hour"),
    },
```

Add `API_THROTTLE_READ=` and `API_THROTTLE_WRITE=` (commented, with the defaults) to
`.env.example`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest src/api -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api src/config/settings/base.py .env.example
git commit -m "feat(api): numbered pagination and per-user read/write throttles"
```

---

### Task 4: Idempotent API writes

**Files:**
- Create: `src/api/idempotency.py`
- Create: `src/api/tests/urls_idempotency.py`, `src/api/tests/test_idempotency.py`

**Interfaces:**
- Consumes: `core.idempotency.run_once(user, scope, key, operation) -> (result, replayed)`.
- Produces:
  - `api.idempotency.Outcome(status: int, kind: str, public_id: str, replayed: bool = False)` (frozen dataclass)
  - `api.idempotency.run_idempotent(request, scope: str, operation: Callable[[], Outcome], *, required: bool = False) -> Outcome`
  - `api.idempotency.mark_replayed(response, outcome) -> response` (sets `Idempotent-Replayed: true` when replayed)
  - `api.idempotency.fingerprint(scope, data, files) -> str`
  - Views re-read the object from `outcome.public_id` through their selector and serialize its current state with `outcome.status`.

**Acceptance Criteria:**
- Same key and same body twice → the operation runs once; the second answer has the original status and `Idempotent-Replayed: true`.
- Same key, different body → 409 `idempotency_key_reused`.
- Missing key when required → 400 `idempotency_key_required`; non-UUID key → 400.
- No key when optional → the operation runs on every call.
- A key stored by a UI form (a plain string result) and replayed through the API → 409, never a crash.
- With the cache raising, a keyed write answers 503 `idempotency_unavailable` and the operation never runs (fail closed).
- An `OSError` raised by the write itself answers 500 `server_error`, not 503.

- [ ] **Step 1: Write the failing tests**

`src/api/tests/urls_idempotency.py`:

```python
import uuid

from django.urls import include, path
from rest_framework.response import Response
from rest_framework.views import APIView

from api.idempotency import Outcome, mark_replayed, run_idempotent

CALLS: list[str] = []


class CreateView(APIView):
    required = False

    def post(self, request):
        def operation():
            public_id = str(uuid.uuid4())
            CALLS.append(public_id)
            return Outcome(status=201, kind="thing", public_id=public_id)

        outcome = run_idempotent(request, "test:create", operation, required=self.required)
        return mark_replayed(Response({"id": outcome.public_id}, status=outcome.status), outcome)


class RequiredCreateView(CreateView):
    required = True


class FailingCreateView(APIView):
    def post(self, request):
        def operation():
            raise OSError("disk full")

        run_idempotent(request, "test:failing", operation)


urlpatterns = [
    path("api/t/create/", CreateView.as_view()),
    path("api/t/create-required/", RequiredCreateView.as_view()),
    path("api/t/create-failing/", FailingCreateView.as_view()),
    path("", include("config.urls")),
]
```

`src/api/tests/test_idempotency.py`:

```python
import uuid

import pytest

from api.tests import urls_idempotency
from core import idempotency

pytestmark = [pytest.mark.django_db, pytest.mark.urls("api.tests.urls_idempotency")]


@pytest.fixture(autouse=True)
def _reset_calls():
    urls_idempotency.CALLS.clear()


@pytest.fixture
def logged(api_client, active_user):
    api_client.force_login(active_user)
    return api_client


def _post(client, path, key, body=None):
    headers = {"HTTP_IDEMPOTENCY_KEY": key} if key is not None else {}
    return client.post(path, body or {"title": "a"}, format="json", **headers)


def test_replay_runs_once_and_keeps_status(logged):
    key = str(uuid.uuid4())
    first = _post(logged, "/api/t/create/", key)
    second = _post(logged, "/api/t/create/", key)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(urls_idempotency.CALLS) == 1
    assert second["Idempotent-Replayed"] == "true"
    assert not first.has_header("Idempotent-Replayed")


def test_key_reused_with_other_body_is_refused(logged):
    key = str(uuid.uuid4())
    _post(logged, "/api/t/create/", key, {"title": "a"})
    response = _post(logged, "/api/t/create/", key, {"title": "b"})
    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_key_reused"


def test_required_key_missing(logged):
    response = _post(logged, "/api/t/create-required/", None)
    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_required"


def test_key_must_be_a_uuid(logged):
    assert _post(logged, "/api/t/create/", "not-a-uuid").status_code == 400


def test_optional_key_absent_runs_every_time(logged):
    _post(logged, "/api/t/create/", None)
    _post(logged, "/api/t/create/", None)
    assert len(urls_idempotency.CALLS) == 2


def test_cache_down_fails_closed(logged, monkeypatch):
    def broken(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(idempotency.cache, "get", broken)
    response = _post(logged, "/api/t/create/", str(uuid.uuid4()))
    assert response.status_code == 503
    assert response.json()["code"] == "idempotency_unavailable"
    assert urls_idempotency.CALLS == []


def test_write_failure_is_a_server_error_not_unavailable(logged):
    response = _post(logged, "/api/t/create-failing/", str(uuid.uuid4()))
    assert response.status_code == 500
    assert response.json()["code"] == "server_error"


def test_ui_stored_result_is_not_replayed_as_api(logged, active_user):
    key = str(uuid.uuid4())
    idempotency.run_once(active_user, "test:create", key, lambda: "ui-public-id")
    response = _post(logged, "/api/t/create/", key)
    assert response.status_code == 409
    assert urls_idempotency.CALLS == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest src/api/tests/test_idempotency.py -q`
Expected: FAIL (`ModuleNotFoundError: api.idempotency`).

- [ ] **Step 3: Implement**

`src/api/idempotency.py`:

```python
"""Idempotent API writes (spec § 2) over ``core.idempotency``, with the Idempotency-Key header.

The stored result is ``{status, kind, public_id, fingerprint}``. A replay with the same key
and the same request returns the original status (the view re-reads and serializes the
object's current state); the same key with a different request answers 409. Scopes are the
UI views' scope strings, so one key can never run a write twice through either door.
"""

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from django.utils.translation import gettext as _
from redis.exceptions import RedisError

from core import idempotency
from core.errors import DomainError

HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"


@dataclass(frozen=True)
class Outcome:
    status: int
    kind: str
    public_id: str
    replayed: bool = False


def _reused() -> DomainError:
    return DomainError(
        "idempotency_key_reused",
        _("This Idempotency-Key was already used for a different request."),
    )


def _key(request, required: bool) -> str | None:
    raw = request.headers.get(HEADER, "").strip()
    if not raw:
        if required:
            raise DomainError(
                "idempotency_key_required",
                _("This request needs an Idempotency-Key header (a UUID)."),
            )
        return None
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        raise DomainError(
            "idempotency_key_required", _("The Idempotency-Key header must be a UUID.")
        ) from None


def fingerprint(scope: str, data, files) -> str:
    """SHA-256 of the scope, the non-file fields and each file's name and size."""
    if hasattr(data, "getlist"):
        fields = {name: data.getlist(name) for name in sorted(data) if name not in files}
    else:
        fields = {name: data[name] for name in sorted(data) if name not in files}
    file_parts = sorted((name, upload.name, upload.size) for name, upload in files.items())
    payload = json.dumps(
        {"scope": scope, "fields": fields, "files": file_parts}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def run_idempotent(
    request, scope: str, operation: Callable[[], Outcome], *, required: bool = False
) -> Outcome:
    key = _key(request, required)
    if key is None:
        return operation()
    print_ = fingerprint(scope, request.data, request.FILES)

    started = False

    def stored() -> dict:
        nonlocal started
        started = True
        outcome = operation()
        return {
            "status": outcome.status,
            "kind": outcome.kind,
            "public_id": outcome.public_id,
            "fingerprint": print_,
        }

    try:
        result, replayed = idempotency.run_once(request.user, scope, key, stored)
    except DomainError:
        raise
    except (ConnectionError, OSError, RedisError) as error:
        if started:
            raise  # the write itself failed: a server error, not a cache outage
        # Fail closed: without the cache we cannot guarantee "at most once" (spec § 16).
        raise DomainError(
            "idempotency_unavailable",
            _("This action is temporarily unavailable. Please try again in a moment."),
        ) from error
    if not isinstance(result, dict) or result.get("fingerprint") != print_:
        raise _reused()
    return Outcome(result["status"], result["kind"], result["public_id"], replayed)


def mark_replayed(response, outcome: Outcome):
    if outcome.replayed:
        response[REPLAYED_HEADER] = "true"
    return response
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest src/api src/core -q`
Expected: PASS (the `core.idempotency` tests are unchanged and still green).

- [ ] **Step 5: Commit**

```bash
git add src/api
git commit -m "feat(api): Idempotency-Key replays with request fingerprint"
```

---

### Task 5: `/tags/`, OpenAPI schema and docs, and the security guard tests

**Files:**
- Modify: `src/api/serializers.py` (add `TagSerializer`), `src/api/views.py` (add `TagListView`), `src/api/urls.py`
- Modify: `src/config/settings/base.py` (`SPECTACULAR_SETTINGS`)
- Create: `src/api/tests/test_tags.py`, `src/api/tests/test_schema_docs.py`, `src/api/tests/test_guards.py`

**Interfaces:**
- Consumes: `taxonomy.models.Tag`, `taxonomy.services.normalize_tag_key(name) -> str`.
- Produces: `api.serializers.TagSerializer` (fields `name`, `slug`); URL names `api:tags` (`/api/v1/tags/`), `api:schema` (`/api/schema/`), `api:docs` (`/api/docs/`).

**Acceptance Criteria:**
- `GET /api/v1/tags/?q=py` returns at most 8 tags whose key starts with the normalized query; `q` shorter than 2 characters returns `[]`.
- `/api/schema/` and `/api/docs/` answer 401 to anonymous users and 200 to active users; the docs HTML has no inline `<script>` body and no CDN URL.
- Every URL pattern under `api/` is a DRF view whose permission classes include `IsActiveVerifiedUser` and whose authentication classes are only `api.authentication.SessionAuthentication`.
- No response carries `Access-Control-Allow-Origin`, even with an `Origin` header.
- A POST without a CSRF token is refused (403) when CSRF checks are enforced.

- [ ] **Step 1: Write the failing tests**

`src/api/tests/test_tags.py`:

```python
import pytest

from taxonomy.models import Tag

pytestmark = pytest.mark.django_db


@pytest.fixture
def logged(api_client, active_user):
    api_client.force_login(active_user)
    return api_client


def test_prefix_search(logged):
    for name in ["Python", "PyTorch", "Pandas", "Rust"]:
        Tag.objects.create(name=name, slug=name.lower())
    names = [tag["name"] for tag in logged.get("/api/v1/tags/?q=py").json()]
    assert names == ["Python", "PyTorch"]


def test_short_query_returns_nothing(logged):
    Tag.objects.create(name="Python", slug="python")
    assert logged.get("/api/v1/tags/?q=p").json() == []


def test_at_most_eight(logged):
    for index in range(12):
        Tag.objects.create(name=f"tag{index:02d}", slug=f"tag{index:02d}")
    assert len(logged.get("/api/v1/tags/?q=tag").json()) == 8
```

`src/api/tests/test_schema_docs.py`:

```python
import re

import pytest

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("path", ["/api/schema/", "/api/docs/"])
def test_anonymous_refused(api_client, path):
    assert api_client.get(path).status_code == 401


def test_schema_for_employees(api_client, active_user):
    api_client.force_login(active_user)
    response = api_client.get("/api/schema/")
    assert response.status_code == 200
    assert b"/api/v1/me/" in response.content


def test_docs_have_no_inline_script_nor_cdn(client, active_user):
    client.force_login(active_user)
    html = client.get("/api/docs/").content.decode()
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html)
    assert "cdn." not in html and "unpkg" not in html
```

`src/api/tests/test_guards.py`:

```python
import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient
from rest_framework.views import APIView

from api.authentication import SessionAuthentication
from api.permissions import IsActiveVerifiedUser

pytestmark = pytest.mark.django_db


def _walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def _api_routes():
    return [(route, view) for route, view in _walk(get_resolver().url_patterns)
            if route.startswith("api/")]


def test_api_routes_exist():
    assert _api_routes()


@pytest.mark.parametrize(("route", "view"), _api_routes())
def test_every_api_route_is_a_guarded_drf_view(route, view):
    view_class = getattr(view, "cls", None)
    assert isinstance(view_class, type) and issubclass(view_class, APIView), route
    assert any(issubclass(p, IsActiveVerifiedUser) for p in view_class.permission_classes), route
    assert view_class.authentication_classes, route
    assert all(issubclass(a, SessionAuthentication) for a in view_class.authentication_classes)


def test_no_cors_header(api_client, active_user):
    api_client.force_login(active_user)
    response = api_client.get("/api/v1/me/", HTTP_ORIGIN="https://evil.example.com")
    assert not response.has_header("Access-Control-Allow-Origin")


def test_csrf_enforced_on_writes(active_user):
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(active_user)
    assert client.post("/api/v1/me/", {}, format="json").status_code == 403
```

Note: `_api_routes()` runs at collection time. `get_resolver()` only needs settings, not the
database.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest src/api -q`
Expected: FAIL (404 on `/api/v1/tags/`, `/api/schema/` and `/api/docs/`).

- [ ] **Step 3: Implement**

Append to `src/api/serializers.py`:

```python
class TagSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)
    slug = serializers.SlugField(read_only=True)
```

Append to `src/api/views.py`, with the imports `from rest_framework.generics import ListAPIView`,
`from taxonomy.models import Tag`, `from taxonomy.services import normalize_tag_key` and
`from .serializers import TagSerializer`:

```python
TAG_SUGGESTIONS = 8


class TagListView(ListAPIView):
    """Tag suggestions: names whose key starts with ``q`` (at least 2 characters)."""

    serializer_class = TagSerializer
    pagination_class = None

    def get_queryset(self):
        query = self.request.query_params.get("q", "").strip()
        if len(query) < 2:
            return Tag.objects.none()
        return Tag.objects.filter(key__startswith=normalize_tag_key(query)).order_by("name")[
            :TAG_SUGGESTIONS
        ]
```

Replace `src/api/urls.py` with:

```python
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerSplitView

from . import views

app_name = "api"

urlpatterns = [
    path("v1/me/", views.MeView.as_view(), name="me"),
    path("v1/tags/", views.TagListView.as_view(), name="tags"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerSplitView.as_view(url_name="api:schema"), name="docs"),
]
```

Add to `src/config/settings/base.py`, after `REST_FRAMEWORK`:

```python
SPECTACULAR_SETTINGS = {
    "TITLE": "Talan Communities API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "SERVE_PERMISSIONS": ["api.permissions.IsActiveVerifiedUser"],
    "SERVE_AUTHENTICATION": ["api.authentication.SessionAuthentication"],
    # Self-hosted Swagger UI (no CDN); the split view serves its JS as a file (no inline script).
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest src/api -q`
Expected: PASS. If `test_csrf_enforced_on_writes` returns 405 instead of 403, DRF checked
the method before CSRF; then post to `/api/v1/tags/` instead (also GET-only). The 403 must
come from CSRF: assert `"CSRF" in response.json()["detail"]`.

- [ ] **Step 5: Commit**

```bash
git add src/api src/config/settings/base.py
git commit -m "feat(api): tag suggestions, OpenAPI schema and self-hosted docs, route guards"
```

---

### Task 6: Gates (schema validation, French catalogue, developer docs)

**Files:**
- Modify: `Makefile` (`lint`)
- Modify: `src/locale/fr/LC_MESSAGES/django.po`
- Modify: `docs/development.md` (new "REST API" section)

**Acceptance Criteria:**
- `make lint` validates the OpenAPI schema with no warning.
- `make i18n-check` passes, and every new string has a French translation.
- `make test` passes with coverage ≥ 85 %; `make security` passes (pip-audit covers the new dependencies).
- `docs/development.md` explains the API conventions in under 40 lines.

- [ ] **Step 1: Add schema validation to `lint`**

In `Makefile`, add as the last line of the `lint` recipe:

```make
	uv run python manage.py spectacular --validate --fail-on-warn --file /dev/null --settings=config.settings.test
```

Run: `make lint`
Expected: PASS. If drf-spectacular warns about `MeView` or `TagListView`, fix the warning
(for example with `@extend_schema(responses=...)`). Never drop `--fail-on-warn`.

- [ ] **Step 2: Translate the new strings**

Run: `make messages`, then fill these `msgstr` in `src/locale/fr/LC_MESSAGES/django.po` and
remove any `#, fuzzy` flag on them:

| msgid | msgstr |
|---|---|
| `API` | `API` |
| `Verify your second factor in the application first.` | `Vérifiez d'abord votre second facteur dans l'application.` |
| `The submitted data is not valid.` | `Les données envoyées ne sont pas valides.` |
| `An unexpected error occurred.` | `Une erreur inattendue s'est produite.` |
| `This request needs an Idempotency-Key header (a UUID).` | `Cette requête nécessite un en-tête Idempotency-Key (un UUID).` |
| `The Idempotency-Key header must be a UUID.` | `L'en-tête Idempotency-Key doit être un UUID.` |
| `This Idempotency-Key was already used for a different request.` | `Cette clé Idempotency-Key a déjà été utilisée pour une autre requête.` |
| `This action is temporarily unavailable. Please try again in a moment.` | `Cette action est momentanément indisponible. Réessayez dans un instant.` |

Run: `make i18n-check`
Expected: PASS.

- [ ] **Step 3: Document the API for developers**

Add to `docs/development.md` a section `## REST API` that covers:
- `/api/v1/` uses DRF with session authentication only, same origin, no CORS, and never accepts credentials.
- The schema is at `/api/schema/` and the docs at `/api/docs/` (login required).
- The default permission `IsActiveVerifiedUser` checks an active account and MFA for privileged roles, answering JSON 401/403.
- The error body, and the status map in `api/exceptions.py`: a new `DomainError` code must be classified there.
- Pagination is 20 per page, max 50; the throttles are set by `API_THROTTLE_READ` / `API_THROTTLE_WRITE`; the business limits stay in the services.
- `Idempotency-Key` (UUID) is required on join, upload and new version; replays set `Idempotent-Replayed`.
- Per-app endpoints go in `<app>/api/{serializers,views,urls}.py`. Views use selectors (never `.objects.`) and services; serializers never save.

- [ ] **Step 4: Run the full gates**

Run: `make lint && make i18n-check && make test && make security`
Expected: all PASS, coverage ≥ 85 %.

- [ ] **Step 5: Commit**

```bash
git add Makefile src/locale docs/development.md
git commit -m "chore(api): validate the OpenAPI schema in lint; docs and French strings"
```

---

## Roadmap: the remaining slices (each gets its own plan after A1 merges)

Every spec requirement maps to a slice. A2–A5 are planned separately because they consume
the names this plan produces (`run_idempotent`, `PagePagination`, `UserSummarySerializer`…).

| Slice | Spec requirements covered |
|---|---|
| **A2 communities** (`communities/api/`) | **First, before any ViewSet:** `api/mixins.py` (`VisibleObjectMixin`, `check_policy`, spec § 2), and harden `src/api/tests/test_guards.py`: strip `^` from `re_path` routes and assert on `view.initkwargs` / ViewSet `@action` permission classes and `get_permissions()` overrides (MFA depends on it). § 3 communities table, including invitations by `{email, role}` with a neutral 202; **required:** the UI join view treats a non-string idempotency replay (an API-stored result) as `idempotency_key_reused`, with a test; the "no `.objects.`" guard either moves the `Tag` lookup of `src/api/views.py` into a taxonomy selector or excludes it explicitly; the parity matrix for communities (3 modes × roles); the guard against `.objects.` in `api/views.py`; constant query count on lists |
| **A3 posts** (`posts/api/`) | § 3 posts table; `FeedCursorPagination` wrapping `posts.selectors.FeedPage`; the shared reaction-count helper extracted from `posts/views_interactions.py`; `permissions` block on detail only; post and comment parity; `edit_conflict` → 409. May split into A3a (posts and feeds) and A3b (comments, reactions, bookmarks, reports) |
| **A4 documents** (`documents/api/`) | § 3 documents table; multipart upload with a required `Idempotency-Key` and the UI scope strings; **required:** the UI create and add-version views treat a non-string idempotency replay as `idempotency_key_reused`, with tests; `download_url` only with `can_download`; document parity (restricted, archived, expired, scan-pending, infected) |
| **A5 docs** | `AGENTS.md` (stack, repository map, URL map, § 9 checklist step "API endpoint + tests", delivery table), `ADR-0003-session-only-same-origin-api.md` + `INDEX.md`, `HANDOFF.md` refresh |
| Later packages | § 8 binding constraint (`X-Background-Request`) → L8; people lookup → L9; global CSP → L11; token auth → R4 + ADR |
| **A1 follow-ups** (from the final review; record as beads once `bd` is available) | idempotency fingerprint: add the request media type, and skip only file parts rather than whole field names (`src/api/idempotency.py`); assert the `api.throttle.unavailable` log in the fail-open test; compare per-hour rates (not counts) in `test_write_rate_covers_business_limits`; set `REST_FRAMEWORK["NUM_PROXIES"]` before the first `AllowAny` endpoint; optionally log API CSRF failures to `django.security.csrf`; CSRF guard test against a POST-capable view |

## Stress Test Results: A1 plumbing plan

### Resolved Decisions
- Inactive accounts: DRF session auth already treats them as anonymous, so they get **401** `not_authenticated`; the unreachable `account_inactive` branch was removed (spec updated).
- Cache (Redis) outage: throttles **fail open** and log, like `posts.ratelimit`; idempotent writes **fail closed** with 503 `idempotency_unavailable`.
- Unhandled API errors: re-raised under `DEBUG`; in production they are logged, `got_request_exception` fires (error collectors), and a JSON 500 is returned with the `request_id`.
- Schema and docs: one schema for all authenticated employees. Accepted, because it contains no data and every action is re-authorized server-side.
- Cross-door replay (API-stored key replayed through a UI form): the UI guard is a required item of A2 (join) and A4 (upload, new version).
- Fail-closed scope: only cache failures **before** the operation starts map to 503; a failure of the write itself propagates as a 500.

### Changes Made
- Task 1: permission, tests and acceptance criteria for inactive accounts.
- Task 2: DEBUG re-raise, `got_request_exception`, two tests; `idempotency_unavailable` classified.
- Task 3: `_FailOpenThrottle` base class and a cache-down test.
- Task 4: fail-closed handling with a `started` flag, two tests, one French string.
- Roadmap: required UI-side replay guards in A2 and A4.

### Deferred / Parking Lot
- UI-side guard against API-stored idempotency results: A2 and A4.

### Confidence Assessment
- Overall: **High.** Every task is TDD with concrete code, and the riskiest behaviours (MFA, CSRF, error mapping, cache outage) have explicit tests.
- Areas of concern: DRF and drf-spectacular version specifics (for example the 401 header scheme, the sidecar settings); the task notes say how to adapt without weakening a check.
