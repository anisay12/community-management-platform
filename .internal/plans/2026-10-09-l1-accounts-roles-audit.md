# Lot L1 — Accounts, Roles, Audit (bilingual EN/FR) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use beads-superpowers:subagent-driven-development (recommended) or beads-superpowers:executing-plans to implement this plan task-by-task. Steps within tasks use checkbox (`- [ ]`) syntax for human readability.

**Goal:** Employees can be provisioned by a functional admin, activate their account, sign in securely (password + lockout, MFA for privileged roles, optional OIDC), manage their profile and language, and every sensitive account action is recorded in an append-only audit log — in English and French.

**Architecture:** New Django apps `accounts`, `organizations`, `audit`, `taxonomy` inside the existing modular monolith (`src/`). Business rules live in `services.py` (transactional writes + `AuditEvent`), `policies.py` (`can_<action>(user, obj) -> bool`) and `selectors.py`; views are thin. Server-rendered Django templates (minimal accessible layout, full design system comes in L2). All UI strings go through gettext with a complete French catalogue.

**Tech Stack:** Django 5.2 LTS, Python 3.13, uv, PostgreSQL 16, Redis 7, Celery 5, django-axes, django-otp (TOTP), argon2-cffi, mozilla-django-oidc, segno (QR SVG), pytest/pytest-django.

**Source spec:** `.internal/specs/2026-10-09-mvp-knowledge-design.md` §4 (being translated from `2026-10-09-mvp-connaissance-design.md`; same content) plus the owner's 2026-10-09 decision: everything in English, application bilingual EN + FR.

## Global Constraints

- Language of code, comments, docstrings, commit messages, docs, PRs: **English**.
- UI is bilingual: `LANGUAGES = [("en", "English"), ("fr", "Français")]`, `LANGUAGE_CODE = "en"`. Resolution order: saved user preference (`UserProfile.language`, empty = automatic) → `django_language` cookie → `Accept-Language` → English. URLs carry **no** language prefix.
- Every user-facing string (templates, forms, messages, emails, model `verbose_name`, choices) uses `gettext`/`gettext_lazy`/`{% translate %}`/`{% blocktranslate %}`. The French catalogue `src/locale/fr/LC_MESSAGES/django.po` must have **no untranslated and no fuzzy entries** (checked by `make i18n-check` in CI). `.mo` files are **not** committed; they are compiled by `make messages`, `make test`, CI and the Dockerfile.
- Each task that adds strings runs `uv run python manage.py makemessages -l fr --no-location --no-obsolete --ignore=.venv` (with `--settings=config.settings.test`) and fills the French translations before committing.
- Identifiers: internal `id` bigint; `public_id` UUIDv4 (unique, `default=uuid.uuid4, editable=False`) is the only identifier in URLs.
- Timestamps `created_at`/`updated_at` (UTC) on business tables.
- Private object not visible to the user → **404**; visible but forbidden action → **403**.
- Every account/role/status change writes an `AuditEvent` **in the same transaction** (`audit.services.record`).
- No secrets in code, images, repo or logs. New secrets are read from env (`django-environ`) with dev-only values in `.env.example` and test-only values in `config/settings/test.py` (setdefault pattern).
- No fake data in production; dev helpers must refuse to run when `DEBUG` is false.
- No modifying request uses GET. CSRF on all forms.
- Role codes (Django `Group.name`): `employee`, `community_creator`, `functional_admin`, `technical_admin`, `auditor`. Labels are translated.
- Ruff config in `pyproject.toml` must pass (`make lint`), coverage gate `--cov-fail-under=85` (`make test`), Bandit `-ll` clean, migrations `makemigrations --check` clean.
- Tests: pytest + pytest-django, PostgreSQL real DB (`config.settings.test`). Tests live in `src/<app>/tests/test_*.py`. Use `django_assert_num_queries` for list pages where noted.
- Commits: conventional style (`feat(accounts): …`), English, small, one per step group, ending with the trailers:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01C66DowjD3hzocrWByXmkrQ`.
- Do not touch `docs/`, `README.md`, `.internal/specs/` (a separate translation is in progress) except where a task explicitly says so.
- Local services for tests: PostgreSQL at `localhost:5432` (postgres/postgres) and Redis at `localhost:6379` are expected; if not running, start them with `docker compose up -d postgres redis` (ports published) or the existing local services.

## Deliberate cuts (surfaced to the owner)

- **Avatar upload UI → L5.** `UserProfile.avatar` field exists in L1, but uploading needs the antivirus scan pipeline and private-file serving (ADR-0001) delivered in L5. Until then initials are shown.
- **Nightly IdP synchronisation (R4)** stays out of scope as in the spec; L1 checks IdP status at each OIDC login only.
- **Anonymization/export of data owned by later lots** (posts, notifications, downloads…) is wired through a registry introduced here; each later lot registers its own exporter/anonymizer.

## File Structure

```
src/
  config/settings/base.py        # apps, middleware, auth, i18n, axes, otp, oidc, sessions, celery beat
  config/settings/test.py        # test-only secrets (AUDIT_IP_HASH_KEY …)
  config/urls.py                 # accounts, manage, i18n, admin on DJANGO_ADMIN_PATH, error handlers
  core/
    context.py                   # client_ip(request), current request context (request_id, ip) contextvar
    middleware.py                # + UserPreferencesMiddleware (language, timezone), ActivityMiddleware
    views_errors.py              # 403/404/429 views, static 500
    templates/base.html, errors/*.html, components/language_switcher.html
    static/core/app.css          # minimal accessible stylesheet (replaced by L2 design system)
    templates/500.html           # static, no template tags needing DB
  locale/fr/LC_MESSAGES/django.po
  taxonomy/  models.py (Tag)
  organizations/ models.py (OrganizationUnit, Employment), admin.py
  audit/ models.py (AuditEvent), services.py (record), signals.py, tasks.py (purge), admin.py (read-only)
  accounts/
    models.py       # User, UserManager, UserProfile, ExternalIdentity, UserSession, DataExport
    roles.py        # Role enum + group helpers
    policies.py     # can_manage_users, can_view_profile, can_view_profile_details, requires_mfa
    selectors.py    # users_for_admin_list, …
    services.py     # create_user, activate, suspend, reactivate, deactivate, set_roles, import_users_csv
    privacy.py      # export/anonymize registries, build_export, anonymize_user
    tokens.py       # activation_token_generator (72 h)
    backends.py     # EmailBackend honouring AUTH_MODE / break-glass
    oidc.py         # TalanOIDCBackend
    forms.py, views_auth.py, views_mfa.py, views_profile.py, views_manage.py, urls.py
    middleware.py   # MFARequiredMiddleware
    tasks.py        # send_email, build_data_export, purge_expired_exports, anonymize_expired_accounts
    admin.py, apps.py, signals.py
    management/commands/disable_local_passwords.py, create_dev_admin.py
    templates/accounts/…, templates/manage/…, templates/emails/…
```

---

### Task 1: Bilingual foundation, base layout, error pages, English codebase

**Files:**
- Modify: `src/config/settings/base.py`, `src/config/settings/prod.py`, `src/config/urls.py`, `src/core/middleware.py` (comments only), any other `src/**/*.py` with French comments/docstrings/messages, `Makefile`, `docker/Dockerfile`, `.github/workflows/ci.yml`, `pyproject.toml` (if needed)
- Create: `src/core/templates/base.html`, `src/core/templates/components/language_switcher.html`, `src/core/templates/errors/403.html`, `404.html`, `429.html`, `src/templates/500.html` (static), `src/core/static/core/app.css`, `src/core/views_errors.py`, `src/core/templates/core/home.html`, `src/locale/fr/LC_MESSAGES/django.po`
- Test: `src/core/tests/test_i18n.py`, `src/core/tests/test_errors.py`

**Interfaces:**
- Produces: template `base.html` with blocks `title`, `content`, `extra_head`; includes skip link (`<a class="skip-link" href="#main">`), `<header>` with site name `{% translate "Communities" %}`, user menu placeholder block `usernav`, language switcher, `<main id="main" tabindex="-1">`, messages rendered in a `role="status"` region; `<html lang="{{ LANGUAGE_CODE }}">`; `<body hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'>`.
- Produces: URL names `home` (`/`, simple page: login link when anonymous, greeting when authenticated), `set_language` (`/i18n/setlang/`, Django's view for now; Task 7 wraps it).
- Produces: `config.urls` sets `handler403 = "core.views_errors.permission_denied"`, `handler404 = "core.views_errors.page_not_found"`, `handler500 = "core.views_errors.server_error"`; `core.views_errors.too_many_requests(request, exception=None)` renders `errors/429.html` with status 429 (used by axes in Task 4).
- Produces: Makefile targets `messages` (makemessages fr + compilemessages) and `i18n-check` (fails if `msgattrib --untranslated` or `msgattrib --only-fuzzy` on the fr catalogue outputs any entry, or if `makemessages` changes the catalogue — compare with `git diff --exit-code src/locale`). `test` target runs `compilemessages` first. `ci` runs `i18n-check`.

**Acceptance Criteria:**
- `LANGUAGE_CODE == "en"`, `LANGUAGES == [("en","English"),("fr","Français")]`.
- `GET /` with `Accept-Language: fr` renders French strings and `<html lang="fr">`; without header renders English.
- POST to `set_language` with `language=fr` sets the cookie and subsequent pages are French.
- 404 page is rendered from the app layout with status 404 in both languages; 403 with status 403; `server_error` returns status 500 with static HTML containing no DB access (test with `django_assert_num_queries(0)`), and displays the request id if present (`request.request_id`), via plain Python string formatting (no template engine).
- French catalogue complete; `make i18n-check` passes.
- No French comments, docstrings or exception messages left in `src/`, `Makefile`, `compose.yaml`, `docker/`, `.github/` (translate them to English). `prod.py` message: `"DJANGO_ALLOWED_HOSTS must list at least one host name."` and adjust the matching test assertion if any.
- Dockerfile build stage installs `gettext` (apt, `--no-install-recommends`, cleaned lists) and runs `compilemessages` with the same throwaway env as `collectstatic`; runtime image contains the `.mo` files and no gettext tooling is required at runtime.
- CI installs `gettext` (`sudo apt-get install -y gettext`) before `make lint`/`make test`, and runs `make i18n-check`.
- Locally, install gettext with `apt-get install -y gettext` if missing.

- [ ] **Step 1: Write failing tests** `src/core/tests/test_i18n.py`:

```python
import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_languages_configured():
    assert settings.LANGUAGE_CODE == "en"
    assert [code for code, _ in settings.LANGUAGES] == ["en", "fr"]


def test_home_defaults_to_english(client):
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert b'<html lang="en"' in response.content


def test_home_follows_accept_language(client):
    response = client.get(reverse("home"), HTTP_ACCEPT_LANGUAGE="fr-FR,fr;q=0.9")
    assert b'<html lang="fr"' in response.content
    assert "Communautés".encode() in response.content


def test_set_language_switches_to_french(client):
    response = client.post(reverse("set_language"), {"language": "fr", "next": "/"})
    assert response.status_code == 302
    assert b'<html lang="fr"' in client.get("/").content


def test_french_catalogue_is_complete():
    from pathlib import Path

    po = Path(settings.LOCALE_PATHS[0]) / "fr" / "LC_MESSAGES" / "django.po"
    text = po.read_text(encoding="utf-8")
    assert "#, fuzzy" not in text.split("\n\n", 1)[1]
    blocks = text.split("\n\n")[1:]
    untranslated = [b for b in blocks if b.rstrip().endswith('msgstr ""') and "msgid_plural" not in b]
    assert untranslated == []
```

and `src/core/tests/test_errors.py` covering 404 (EN + FR, status, layout landmark `<main`), 403 via a test-only view raising `PermissionDenied` (use `urls` override with `pytest.mark.urls` and a module `core.tests.urls_errors`), 429 view, and 500 (`server_error` called directly with `RequestFactory`, `django_assert_num_queries(0)`, contains the request id).

- [ ] **Step 2: Run** `uv run pytest src/core/tests/test_i18n.py src/core/tests/test_errors.py -v` → FAIL.
- [ ] **Step 3: Implement** settings, templates, views, CSS (readable defaults, visible focus outline, `.skip-link` visible on focus, ≥44px buttons), switcher (`<form method="post" action="{% url 'set_language' %}">` with a `<select name="language">` labelled `{% translate "Language" %}` and a submit button; options show each language in its own name), Makefile, Dockerfile, CI, French catalogue, English comments.
- [ ] **Step 4: Run** `make lint && make test && make i18n-check` → PASS.
- [ ] **Step 5: Commit** `feat(i18n): bilingual EN/FR foundation, base layout and error pages`.

---

### Task 2: Domain models — users, profiles, organizations, tags, roles

**Files:**
- Create: `src/taxonomy/{__init__,apps,models,admin}.py` + migration; `src/organizations/{__init__,apps,models,admin}.py` + migration; `src/accounts/{__init__,apps,models,roles,admin}.py` + migrations (including a data migration creating the five groups)
- Modify: `src/config/settings/base.py` (`INSTALLED_APPS`, `AUTH_USER_MODEL = "accounts.User"`)
- Test: `src/accounts/tests/test_models.py`, `src/organizations/tests/test_models.py`, `src/taxonomy/tests/test_models.py`

**Interfaces (Produces):**
- `taxonomy.Tag(name CharField(64), slug SlugField(unique), created_at)`; unique constraint on `Lower("name")` named `taxonomy_tag_name_ci_unique`.
- `accounts.User(AbstractBaseUser, PermissionsMixin)`:
  - `public_id` UUID unique; `email` EmailField(254); `first_name`, `last_name` CharField(150, blank allowed for anonymized users); `status` CharField choices `User.Status` (TextChoices: `PENDING="pending"`, `ACTIVE="active"`, `SUSPENDED="suspended"`, `DEACTIVATED="deactivated"`), default `pending`, indexed; `is_staff` bool; `last_seen_at` DateTime null; `activated_at`, `deactivated_at`, `anonymized_at` DateTime null; `created_at`, `updated_at`.
  - `is_active = models.GeneratedField(expression=Case(When(status="active", then=Value(True)), default=Value(False)), output_field=BooleanField(), db_persist=True)` — single source of truth, filterable.
  - Constraint `UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique")`.
  - `USERNAME_FIELD = "email"`, `EMAIL_FIELD = "email"`, `REQUIRED_FIELDS = ["first_name", "last_name"]`.
  - `get_full_name()` → `"First Last"` stripped, or `gettext("Former employee")` when `anonymized_at` is set; `get_short_name()` → first name.
  - `save()` normalizes email (`UserManager.normalize_email` + full lowercase).
  - Manager `UserManager(BaseUserManager)`: `create_user(email, password=None, **extra)` (status default pending, `set_unusable_password()` when no password), `create_superuser(email, password, **extra)` (status active, is_staff, is_superuser, activated_at now), `get_by_natural_key(email)` case-insensitive (`email__iexact`).
- `accounts.UserProfile(user OneToOne related_name="profile", job_title CharField(150, blank), bio TextField(blank, max 2000 via MaxLengthValidator), avatar ImageField(upload_to="avatars/", blank, null) — upload UI deferred to L5, timezone CharField(64, default "Europe/Paris", validated against zoneinfo.available_timezones()), language CharField(8, choices [("", "Automatic"), ("en","English"),("fr","Français")], blank), interests M2M taxonomy.Tag blank, profile_visibility choices `UserProfile.Visibility` (`PRIVATE="private"`, `COMMUNITIES="communities"`, `COMPANY="company"`, default company), is_discoverable bool default True, updated_at)`. Profile auto-created by a `post_save` signal on User creation (in `accounts/signals.py`, connected in `AppConfig.ready`).
- `organizations.OrganizationUnit(name CharField(200), code CharField(50, unique), parent FK self null PROTECT related_name="children", created_at, updated_at)`; `__str__` = `"{code} — {name}"`.
- `organizations.Employment(user OneToOne related_name="employment", unit FK OrganizationUnit PROTECT, manager FK User null SET_NULL related_name="reports", updated_at)`; `CheckConstraint(condition=~Q(manager=F("user")), name="organizations_employment_manager_not_self")`.
- `accounts.roles`: `class Role(models.TextChoices)` with codes above and lazy-translated labels (Employee, Community creator, Functional administrator, Technical administrator, Auditor); `PRIVILEGED_ROLES = {Role.FUNCTIONAL_ADMIN, Role.TECHNICAL_ADMIN}`; `def user_roles(user) -> set[str]`; `def has_role(user, role) -> bool` (False for anonymous); `def is_manager(user) -> bool` (`user.reports.exists()`).
- `accounts.models.ExternalIdentity(user FK CASCADE related_name="external_identities", provider CharField(50), subject CharField(255), created_at, last_login_at null)`; `UniqueConstraint(fields=["provider","subject"], name="accounts_externalidentity_provider_subject_unique")`; `provider`/`subject` immutable (`save()` raises `ValueError` when changing them on an existing row). Used by OIDC in Task 9.
- Data migration creates the five groups (idempotent `get_or_create`), reversible (no-op reverse).
- Admin registrations: `UserAdmin` (list email/name/status/is_staff, search email/name, readonly public_id/last_seen/created), OrganizationUnit, Employment, Tag. (Admin URL wiring comes in Task 5.)

**Acceptance Criteria:**
- Creating two users whose emails differ only by case raises `IntegrityError`.
- `User.objects.get_by_natural_key("ALICE@Example.com")` finds `alice@example.com`.
- `is_active` is True only for status active and is filterable (`User.objects.filter(is_active=True)`).
- Employment with manager == user raises `IntegrityError`.
- Profile is auto-created; bio > 2000 chars fails `full_clean()`; invalid timezone fails `full_clean()`.
- The five groups exist after migrate.
- `makemigrations --check` clean; migrations apply on an empty DB.

- [ ] **Step 1:** Write the failing tests listed above (one test per criterion, `pytest.mark.django_db`).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement models, manager, signals, roles, admin, migrations (`makemigrations accounts organizations taxonomy`, then `makemigrations accounts --empty -n create_role_groups`).
- [ ] **Step 4:** `make lint && make test && make i18n-check` → PASS (update fr catalogue for new labels).
- [ ] **Step 5:** Commit `feat(accounts): user, profile, organization and role models`.

---

### Task 3: Append-only audit log

**Files:**
- Create: `src/audit/{__init__,apps,models,services,signals,tasks,admin}.py` + migration; `src/core/context.py`
- Modify: `src/core/middleware.py` (RequestIDMiddleware also sets the request context), `src/config/settings/base.py` (`AUDIT_IP_HASH_KEY = env("AUDIT_IP_HASH_KEY")`, `AUDIT_RETENTION_DAYS = env.int(..., default=365)`, `NUM_PROXIES = env.int("NUM_PROXIES", default=0)`, `CELERY_BEAT_SCHEDULE` entry), `src/config/settings/test.py`, `.env.example`, `compose.yaml` (env passthrough if needed)
- Test: `src/audit/tests/test_audit.py`, `src/core/tests/test_context.py`

**Interfaces (Produces):**
- `core.context.client_ip(request) -> str | None`: if `settings.NUM_PROXIES > 0`, take the `NUM_PROXIES`-th address from the right of `X-Forwarded-For` (stripped; fall back to `REMOTE_ADDR` if the header is short); else `REMOTE_ADDR`. Validates with `ipaddress.ip_address`, returns None on invalid.
- `core.context.get_request_context() -> RequestContext` (dataclass `request_id: str | None`, `ip: str | None`) backed by a `contextvars.ContextVar`; `core.context.bind_request(request)` / `clear_request()` called by `RequestIDMiddleware`.
- `audit.models.AuditEvent(actor FK AUTH_USER_MODEL null SET_NULL related_name="+", action CharField(64, db_index), target_type CharField(64), target_id CharField(64), community_id BigIntegerField null (FK to communities added in L3), changes JSONField default dict, ip_hash CharField(64, blank), request_id CharField(64, blank), created_at DateTimeField(default=timezone.now, db_index))`; ordering `-created_at`; index on `(target_type, target_id)`.
  - Append-only: `save()` raises `audit.models.AuditImmutableError` when `self.pk` already exists (`_state.adding` False); `delete()` raises; custom QuerySet `update()`/`delete()` raise. A classmethod `AuditEvent.purge_older_than(cutoff) -> int` uses `QuerySet._raw_delete` for retention only.
- `audit.services.record(*, actor, action: str, target, changes: dict | None = None, community_id: int | None = None) -> AuditEvent`: `target` is a model instance (`target_type = target._meta.label_lower`, `target_id = str(getattr(target, "public_id", target.pk))`) **or** a `(type, id)` tuple. Fills `request_id` and `ip_hash = hmac.new(settings.AUDIT_IP_HASH_KEY.encode(), ip.encode(), sha256).hexdigest()` from the request context. Sensitive keys (`password`, `token`, `secret`, `otp`, any key containing them) are removed from `changes`. `actor` may be None (system) or AnonymousUser (stored as None).
- `audit.signals`: `m2m_changed` on `User.groups.through` → for `post_add`/`post_remove`/`post_clear`, record `action="user.roles_changed"` with `changes={"added": [...]} / {"removed": [...]}` (group names), actor from the request context user if available (store the current user in the request context in `bind_request`, set lazily after auth: add `core.context.set_user(user)` called by a tiny middleware placed after `AuthenticationMiddleware`, `core.middleware.RequestUserContextMiddleware`).
- `audit.tasks.purge_audit_events()` (Celery, daily at 03:15 via `CELERY_BEAT_SCHEDULE`): deletes events older than `AUDIT_RETENTION_DAYS`, logs the count.
- `audit.admin`: read-only ModelAdmin (no add/change/delete permissions).
- Docs note (in `docs/development.md` later, Task 9): production DB role has no DELETE on `audit_auditevent` except the purge job role.

**Acceptance Criteria:**
- `record()` stores request_id and an HMAC of the client IP (never the raw IP); with `NUM_PROXIES=1` it uses the right-most forwarded address.
- Updating or deleting an AuditEvent (instance or queryset) raises `AuditImmutableError`.
- Adding a user to a group (directly through `user.groups.add`) creates a `user.roles_changed` event.
- `changes={"password": "x", "email": "a@b"}` stores only `email`.
- Purge removes only events older than the retention.
- `AUDIT_IP_HASH_KEY` missing in env → settings import fails (no default) except test settings which set a test-only value.

- [ ] **Step 1:** Write failing tests for each criterion.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make lint && make test && make i18n-check` → PASS.
- [ ] **Step 5:** Commit `feat(audit): append-only audit log with hashed IP and request correlation`.

---

### Task 4: Local authentication — login, lockout, sessions, password reset, activation

**Files:**
- Create: `src/accounts/{backends,tokens,forms,views_auth,urls,tasks,middleware}.py`, templates `accounts/login.html`, `accounts/password_reset_form.html`, `password_reset_done.html`, `password_reset_confirm.html`, `password_reset_complete.html`, `accounts/activate.html`, `accounts/activation_invalid.html`, emails `emails/password_reset_subject.txt`, `emails/password_reset_body.txt`, `emails/activation_subject.txt`, `emails/activation_body.txt`
- Modify: `pyproject.toml`/`uv.lock` (`uv add django-axes argon2-cffi`), settings base/test, `src/config/urls.py`, `src/core/middleware.py` (ActivityMiddleware)
- Test: `src/accounts/tests/test_auth.py`, `test_activation.py`, `test_password_reset.py`, `test_sessions.py`

**Interfaces (Produces):**
- Settings: `PASSWORD_HASHERS` argon2 first (test keeps MD5); `AUTH_PASSWORD_VALIDATORS` = UserAttributeSimilarity, MinimumLength(min_length=12), CommonPassword, NumericPassword; `AUTHENTICATION_BACKENDS = ["axes.backends.AxesStandaloneBackend", "accounts.backends.EmailBackend"]`; `LOGIN_URL = "accounts:login"`, `LOGIN_REDIRECT_URL = "home"`, `LOGOUT_REDIRECT_URL = "accounts:login"`; `PASSWORD_RESET_TIMEOUT = 3600`; `ACCOUNT_ACTIVATION_TIMEOUT = 72 * 3600`; `SESSION_COOKIE_AGE = 8 * 3600`, `SESSION_EXPIRE_AT_BROWSER_CLOSE = False`, `SESSION_COOKIE_HTTPONLY = True`, `SESSION_COOKIE_SAMESITE = "Lax"` (Secure in prod already); axes: `AXES_FAILURE_LIMIT = 5`, `AXES_COOLOFF_TIME = timedelta(minutes=15)`, `AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]`, `AXES_USERNAME_FORM_FIELD = "username"`, `AXES_RESET_ON_SUCCESS = True`, `AXES_LOCKOUT_CALLABLE = "core.views_errors.too_many_requests"`, `AXES_IPWARE_PROXY_COUNT = NUM_PROXIES`, `AXES_IPWARE_META_PRECEDENCE_ORDER = ["HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"]`, `AXES_ENABLE_ADMIN = False`; `axes` in INSTALLED_APPS and `axes.middleware.AxesMiddleware` last in MIDDLEWARE.
- `accounts.backends.EmailBackend(ModelBackend)`: case-insensitive lookup; constant-time behaviour on unknown user (run the hasher); `user_can_authenticate` → `user.status == ACTIVE`. Local password login allowed only when `settings.AUTH_MODE in {"local", "mixed"}` or the user is the break-glass account (`settings.BREAK_GLASS_EMAIL`, compared case-insensitively) — define `AUTH_MODE = env("AUTH_MODE", default="local")` validated against the three values (ImproperlyConfigured otherwise) and `BREAK_GLASS_EMAIL = env("BREAK_GLASS_EMAIL", default="")` in this task; OIDC itself is Task 8.
- `accounts.forms.LoginForm(AuthenticationForm)`: field `username` labelled "Email" (`type=email`, `autocomplete="username"`), password `autocomplete="current-password"`. Error for any failure is the single neutral message `gettext("Invalid email or password.")`, **except** when the password is correct and the account is `pending` or `suspended` → message `gettext("Your account is not active yet. Check your activation email or contact an administrator.")` / `gettext("Your account is suspended. Contact an administrator.")`; deactivated → neutral message. (Status is only revealed after a correct password.)
- Views (namespace `accounts`, mounted at `/accounts/`): `login` (`LoginView` with `redirect_authenticated_user=True`, rotates session key via `login()`), `logout` (POST only), `password_reset` (always shows the same done page; email sent via Celery task only to active users), `password_reset_done`, `password_reset_confirm` (`<uidb64>/<token>/`), `password_reset_complete`, `activate` (`activate/<uidb64>/<token>/` — GET shows set-password form, POST sets password with validators, sets status active + `activated_at`, audits `user.activated`, logs the user in, redirects home); invalid/expired token → `activation_invalid.html` with status 400.
- `accounts.tokens.activation_token_generator`: subclass of `PasswordResetTokenGenerator` with `key_salt = "accounts.tokens.ActivationTokenGenerator"`, `_make_hash_value` including `user.pk`, `user.status`, `user.password`, timestamp; `check_token` enforces `settings.ACCOUNT_ACTIVATION_TIMEOUT` instead of `PASSWORD_RESET_TIMEOUT` (copy Django 5.2's check logic, constant-time compare). Token invalid once status is not pending.
- `accounts.tasks.send_email(*, to: str, subject_template: str, body_template: str, context: dict, language: str)` Celery task, `autoretry_for=(SMTPException, OSError)`, `retry_backoff=True`, `max_retries=5`; renders templates under `translation.override(language)`.
- `accounts.services.send_activation_email(user, *, request=None)` (stub in this task, reused in Task 6) builds an absolute URL from `settings.SITE_URL` (`env("SITE_URL", default="http://localhost:8000")`) and enqueues `send_email` in the user's language (profile.language or `settings.LANGUAGE_CODE`).
- `accounts.models.UserSession(user FK CASCADE related_name="tracked_sessions", session_key CharField(40, unique), created_at)` populated on `user_logged_in`, removed on `user_logged_out`; `accounts.services.end_all_sessions(user) -> int` deletes the stored sessions through `SessionStore(session_key).delete()` and the rows.
- `core.middleware.ActivityMiddleware` (after AuthenticationMiddleware): for authenticated users, if `last_seen_at` is None or older than 5 minutes → `User.objects.filter(pk=...).update(last_seen_at=now)` and `request.session.modified = True` (refreshes the 8 h inactivity expiry at most every 5 min). Also activates `zoneinfo.ZoneInfo(profile.timezone)` via `timezone.activate` (deactivate otherwise).
- `user_login_failed`/`user_logged_in` signals → `AuditEvent` actions `auth.login_failed` (target `("accounts.user", "unknown")` unless the email matches a user → that user's public_id; changes `{}`) and `auth.login`; `user_logged_out` → `auth.logout`.

**Acceptance Criteria:**
- Active user logs in with correct email in any case → 302 to home, session key changed.
- Wrong password → 200 with "Invalid email or password." and no hint whether the email exists (same response for unknown email).
- Pending or suspended user with correct password → stays on login page with the specific message (spec: "redirected to login with message").
- 5 failures for the same (email, IP) → 6th attempt returns 429 page even with the correct password; other IP not locked.
- Password reset: same response and redirect for existing and unknown emails; email sent only to existing active user; token expires after 1 h (`freezegun` not required: patch `PasswordResetTokenGenerator._now`, or test with `settings.PASSWORD_RESET_TIMEOUT` override).
- Activation link: valid within 72 h, rejected after (override setting / patch `_now`), rejected after use; password validators enforced (11-char password rejected).
- Suspending a user (via `end_all_sessions`) invalidates their existing session: the next request is anonymous.
- `last_seen_at` updated at most once per 5 minutes (two requests → one UPDATE).
- Logout requires POST.
- All pages render in EN and FR.

- [ ] **Step 1:** Write failing tests for each criterion.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** `uv add django-axes argon2-cffi`, implement.
- [ ] **Step 4:** `make lint && make test && make i18n-check` → PASS.
- [ ] **Step 5:** Commit `feat(accounts): email login with lockout, activation, password reset and sessions`.

---

### Task 5: MFA (TOTP) for privileged users and hidden Django admin

**Files:**
- Create: `src/accounts/views_mfa.py`, templates `accounts/mfa_setup.html`, `accounts/mfa_verify.html`; `src/core/admin_site.py`
- Modify: `uv add django-otp segno`; settings (`django_otp`, `django_otp.plugins.otp_totp`, `django_otp.plugins.otp_static` in apps; `django_otp.middleware.OTPMiddleware` right after AuthenticationMiddleware; `OTP_TOTP_ISSUER = "Talan Communities"`; `DJANGO_ADMIN_PATH = env("DJANGO_ADMIN_PATH", default="django-admin/")` must end with "/"); `src/accounts/middleware.py` (MFARequiredMiddleware); `src/config/urls.py`; `src/accounts/policies.py`
- Test: `src/accounts/tests/test_mfa.py`, `src/core/tests/test_admin_site.py`

**Interfaces (Produces):**
- `accounts.policies.requires_mfa(user) -> bool`: authenticated and (`is_staff` or `is_superuser` or has a role in `PRIVILEGED_ROLES` or is the break-glass account).
- `accounts.middleware.MFARequiredMiddleware` (after OTPMiddleware): if `requires_mfa(user)` and not `user.is_verified()` → redirect to `accounts:mfa_setup` when the user has no confirmed TOTP device, else `accounts:mfa_verify`, preserving `next`. Allow-list: `accounts:logout`, the MFA URLs, `set_language`, static, healthz/readyz/metrics.
- `mfa_setup` (GET shows QR code as inline SVG generated with `segno` from the device's `config_url`, plus the secret in base32 for manual entry; POST with a valid 6-digit code confirms the device, calls `django_otp.login`, audits `auth.mfa_enrolled`). Unconfirmed device reused across GETs (not regenerated every time).
- `mfa_verify` (POST code → `django_otp.match_token`/device.verify_token; success → `otp_login`, audit `auth.mfa_verified`; failure → neutral error; failures count towards axes? No — rate-limit with a cache counter: 5 failures per user per 15 min → 429).
- `core.admin_site.SecureAdminSite(AdminSite)`: `has_permission(request)` = active, staff, `request.user.is_verified()`; `admin_view` wrapper raises `Http404` when not permitted (instead of redirecting to login); `login` view → `Http404`. Replace default site (`AdminConfig` subclass `core.apps.SecureAdminConfig` with `default_site = "core.admin_site.SecureAdminSite"` in INSTALLED_APPS instead of `django.contrib.admin`). URL: `path(settings.DJANGO_ADMIN_PATH, admin.site.urls)`.

**Acceptance Criteria:**
- A `functional_admin` user after password login is redirected to MFA setup on any page; after a valid TOTP code (generate via `django_otp.oath.totp(device.bin_key)`) can access pages; a wrong code is rejected.
- An `employee` is never asked for MFA.
- `employee` → 404 on the admin path (index and a model changelist); anonymous → 404; staff without verified OTP → 404 (or redirect to MFA via middleware — assert not 200 and no admin content); staff verified → 200.
- 6th wrong MFA code in 15 min → 429.
- Enrollment and verification are audited.

- [ ] Steps 1–5 as usual. Commit `feat(accounts): mandatory TOTP for privileged users and hidden admin`.

---

### Task 6: Account administration — create, CSV import, status, roles

**Files:**
- Create: `src/accounts/{services,selectors,policies(extend),views_manage}.py`, `src/accounts/forms.py` (extend), templates `manage/user_list.html`, `manage/user_form.html`, `manage/user_detail.html`, `manage/user_import.html`, `manage/user_import_result.html`
- Modify: `src/accounts/urls.py` (namespace `manage`, mounted at `/manage/`), `src/config/urls.py`, `src/core/templates/base.html` (Administration link when `can_manage_users`)
- Test: `src/accounts/tests/test_services.py`, `test_manage_views.py`, `test_policies.py`, `test_csv_import.py`

**Interfaces (Produces):**
- `accounts.policies.can_manage_users(user) -> bool`: active and (superuser or role `functional_admin`). MFA is enforced by the middleware.
- `accounts.services` (all `@transaction.atomic`, all audited, raise `core.errors.DomainError(code, message)` — create `src/core/errors.py` with `class DomainError(Exception)` having `code` and `message` attributes):
  - `create_user(*, actor, email, first_name, last_name, unit=None, manager=None, roles=("employee",), send_activation=True) -> User` — status pending, unusable password, Employment if unit, roles groups, audit `user.created`, activation email enqueued with `transaction.on_commit`. Duplicate email (case-insensitive) → `DomainError("email_taken", …)`.
  - `suspend_user(*, actor, user)`, `reactivate_user(*, actor, user)` (suspended → active), `deactivate_user(*, actor, user)` (sets `deactivated_at`; any status → deactivated), `resend_activation(*, actor, user)` (pending only). Suspend/deactivate call `end_all_sessions`. Actor cannot suspend/deactivate themselves (`DomainError("self_action")`). Audits `user.suspended`, `user.reactivated`, `user.deactivated`, `user.activation_resent` with `changes={"status": [old, new]}`.
  - `set_roles(*, actor, user, roles: Iterable[str])` — validates codes, replaces role groups (other groups untouched); only superusers may grant/revoke `technical_admin` (`DomainError("forbidden_role")`); audit `user.roles_changed` with `{"before": [...], "after": [...]}` (the m2m signal from Task 3 must not double-log when called from the service: the service sets a context flag `core.context.suppress_m2m_audit()` context manager).
  - `import_users_csv(*, actor, file) -> ImportResult` (dataclass `created: int`, `errors: list[ImportError(line: int, message: str)]`). CSV UTF-8 (BOM tolerated), delimiter auto-detected among `,` and `;`, header required exactly `email,first_name,last_name,unit_code,manager_email`; max 5,000 rows and 2 MB; validation pass first (email format, duplicates in file and DB, unknown unit, manager email must exist in DB or earlier/later in the file, manager ≠ self); **if any error, nothing is created**; otherwise all users created in one transaction (managers resolved after creation) and activation emails enqueued on commit. Audit one `user.imported` event per user plus a summary `users.csv_import` event with counts.
- `accounts.selectors.users_for_admin(*, query: str = "", status: str = "") -> QuerySet[User]` (select_related employment/unit, prefetch groups, ordered by last_name, first_name).
- Views (all → 404 unless `can_manage_users`): `manage:user_list` (`/manage/users/`, search + status filter, paginated 20, query count bounded: assert ≤ 8 queries for 25 users), `manage:user_create`, `manage:user_detail` (`/manage/users/<uuid:public_id>/`), POST actions `manage:user_status` (action ∈ suspend/reactivate/deactivate/resend_activation) and `manage:user_roles`, `manage:user_import` (upload + result page listing line errors). Domain errors shown as messages; forms show inline errors with an error summary linking to fields.

**Acceptance Criteria:**
- Employee → 404 on every `/manage/` URL; functional admin (MFA verified in tests via `django_otp` test helper: create a confirmed TOTP device and set the session's `otp_device_id`, or log in with `client.force_login` + patch `user.is_verified`) → 200.
- Creating a user sends one activation email (after commit) and writes `user.created`.
- Changing roles writes exactly one `user.roles_changed` event with before/after.
- CSV with one invalid line among valid lines → result lists the line number and message, **zero** users created.
- Valid CSV with manager defined later in the file → all created, Employment.manager set.
- Suspending a logged-in user ends their session.
- A functional admin cannot grant `technical_admin`.

- [ ] Steps 1–5. Commit `feat(accounts): user administration, CSV import, statuses and roles`.

---

### Task 7: Profile and preferences

**Files:**
- Create: `src/accounts/views_profile.py`, templates `accounts/profile_detail.html`, `accounts/profile_edit.html`, `accounts/preferences.html`; `src/core/views_i18n.py` (`set_language` wrapper)
- Modify: `src/accounts/policies.py`, `src/accounts/forms.py`, `src/core/middleware.py` (UserPreferencesMiddleware), `src/config/urls.py`, base template user menu (Profile, Preferences, Log out button)
- Test: `src/accounts/tests/test_profile.py`, `src/core/tests/test_language_preference.py`

**Interfaces (Produces):**
- `accounts.policies.can_view_profile(viewer, owner) -> bool`: viewer active and (viewer == owner or owner.status == active or `can_manage_users(viewer)`); deactivated/pending/suspended owners → only self/admins.
- `accounts.policies.can_view_profile_details(viewer, owner) -> bool` (bio + interests): self or admins always; else by `owner.profile.profile_visibility`: company → any active viewer; communities → `shares_community(viewer, owner)` (function in `accounts/policies.py` returning False until L3 adds communities; documented); private → no.
- Routes: `accounts:profile_me` (`/me/` → redirect to own profile), `accounts:profile_detail` (`/people/<uuid:public_id>/`, 404 when not `can_view_profile`), `accounts:profile_edit` (`/me/edit/`: job_title, bio, interests (comma-separated tag names, normalized, max 10, each ≤ 64 chars, created via get_or_create on case-insensitive name), profile_visibility, is_discoverable), `accounts:preferences` (`/me/preferences/`: language, timezone (select), saves profile and, when language set, sets the language cookie and activates it).
- `core.views_i18n.set_language`: wraps Django's `set_language`; if the user is authenticated, also saves `profile.language`. URL name stays `set_language`.
- `core.middleware.UserPreferencesMiddleware` (after AuthenticationMiddleware, before views): if authenticated and `profile.language` set → `translation.activate(lang)`, `request.LANGUAGE_CODE = lang`; on the response, set the `django_language` cookie if it differs (so logout keeps the language). Timezone activation lives in ActivityMiddleware (Task 4) — move it here if cleaner, keep a single place.
- Profile page shows name, job title, unit, initials avatar (no upload yet: text "Profile photos will be available soon" is NOT shown — just initials), bio + interests only when allowed; dates in the viewer's timezone.

**Acceptance Criteria:**
- Viewer sees another active user's name/job title; bio hidden when owner visibility is `private` or `communities` (no shared community yet), shown when `company`.
- Profile of a deactivated user → 404 for an employee, 200 for a functional admin.
- Unknown public_id → 404.
- Editing own profile works; bio > 2000 chars shows an inline error; no route lets a user edit another user's profile.
- A user with language preference `fr` sees French even with `Accept-Language: en`; switching via `set_language` while logged in persists the preference.
- Timezone preference changes rendered datetimes (e.g. `last_seen_at` on profile shown in viewer's zone).

- [ ] Steps 1–5. Commit `feat(accounts): profile, visibility rules and language/timezone preferences`.

---

### Task 8: GDPR — data export and anonymization

**Files:**
- Create: `src/accounts/privacy.py`, templates `accounts/data_export.html`, `manage/user_anonymize_confirm.html`
- Modify: `src/accounts/models.py` (DataExport) + migration, `src/accounts/tasks.py`, `src/accounts/services.py`, views/urls, settings (`ACCOUNT_ANONYMIZE_AFTER_DAYS = env.int(..., default=1095)`, `DATA_EXPORT_TTL_DAYS = 7`, beat entries daily 03:30 and 03:45)
- Test: `src/accounts/tests/test_privacy.py`

**Interfaces (Produces):**
- `accounts.privacy.register_exporter(name: str, fn: Callable[[User], dict | list])`, `register_anonymizer(fn: Callable[[User], None])`; built-in exporter `account` (user identity fields, status, dates, profile fields, interests, employment unit code/manager email, roles, external identity providers without subjects) and `audit` (events where actor = user: action, target_type, created_at). Later lots register more.
- `accounts.models.DataExport(public_id, user FK CASCADE, status ∈ pending/ready/failed, file FileField(upload_to="exports/", blank), created_at, expires_at null)`.
- `accounts.services.request_data_export(*, user) -> DataExport` (one pending/ready-unexpired export at a time → returns existing), audited `user.data_export_requested`; Celery `accounts.tasks.build_data_export(export_id)` writes JSON (`{"generated_at": …, "sections": {...}}`, UTF-8, indent 2) to default storage, sets ready and `expires_at = now + 7 days`.
- Views: `accounts:data_export` (`/me/data-export/`: GET status page, POST requests export), `accounts:data_export_download` (`/me/data-export/<uuid:public_id>/download/`): only owner, only ready and not expired, else 404; streams the file with `FileResponse(as_attachment=True, filename="my-data.json")`, `Cache-Control: no-store`. (Will switch to X-Accel-Redirect in L5 per ADR-0001.)
- `accounts.tasks.purge_expired_exports()` deletes expired files + rows.
- `accounts.privacy.anonymize_user(*, actor, user)` (`@transaction.atomic`): requires status deactivated (else DomainError), idempotent; clears profile (job_title, bio, interests, avatar file deleted, language, visibility reset), deletes Employment, ExternalIdentity, UserSession (+ sessions), DataExport (+ files), OTP devices; `first_name = last_name = ""`; `email = f"anonymized-{public_id}@invalid.invalid"`; `set_unusable_password()`; `anonymized_at = now`; runs registered anonymizers; audits `user.anonymized` with no personal data in `changes`.
- `manage:user_anonymize` (GET confirm page, POST performs) for `can_manage_users`.
- `accounts.tasks.anonymize_expired_accounts()` anonymizes users with `deactivated_at <= now - ACCOUNT_ANONYMIZE_AFTER_DAYS` and `anonymized_at is null` (actor None).

**Acceptance Criteria:**
- Export contains the user's own data and nothing about other users; download by another user → 404; after expiry → 404 and purge deletes the file.
- Anonymize: email/name/profile wiped, `get_full_name()` returns "Former employee" (FR "Ancien collaborateur"), audit events keep `actor_id`, user cannot log in, re-running is a no-op.
- Active user cannot be anonymized.
- Scheduled task anonymizes only accounts deactivated more than 3 years ago.

- [ ] Steps 1–5. Commit `feat(accounts): personal data export and anonymization`.

---

### Task 9: OIDC single sign-on modes and break-glass account

**Files:**
- Create: `src/accounts/oidc.py`, `src/accounts/management/commands/disable_local_passwords.py`
- Modify: `uv add mozilla-django-oidc`; settings (OIDC_* from env, enabled only when `AUTH_MODE != "local"`: `OIDC_RP_CLIENT_ID`, `OIDC_RP_CLIENT_SECRET`, `OIDC_OP_AUTHORIZATION_ENDPOINT`, `OIDC_OP_TOKEN_ENDPOINT`, `OIDC_OP_USER_ENDPOINT`, `OIDC_OP_JWKS_ENDPOINT`, `OIDC_RP_SIGN_ALGO="RS256"`, `OIDC_RP_SCOPES="openid email profile"`, `OIDC_PROVIDER_NAME = env(..., default="entra")`; backend appended to `AUTHENTICATION_BACKENDS` only in SSO modes; `mozilla_django_oidc` in INSTALLED_APPS always (urls included only in SSO modes)); `accounts/views_auth.py` + login template (SSO button in mixed/sso_only; local form hidden in sso_only and POST refused unless break-glass email); `accounts/backends.py`
- Test: `src/accounts/tests/test_oidc.py`, `test_auth_modes.py`

**Interfaces (Produces):**
- `accounts.models.ExternalIdentity` already exists (Task 2).
- `accounts.oidc.TalanOIDCBackend(OIDCAuthenticationBackend)`:
  - `verify_claims`: requires `sub` (or `oid` preferred when present) and `email`.
  - `filter_users_by_claims(claims)`: by `ExternalIdentity(provider, subject)`; if none, first link by case-insensitive email **only if** that user has no identity for this provider; returns queryset.
  - `create_user(claims)`: creates a `pending` user (names from `given_name`/`family_name`), identity linked, audit `user.created_from_sso`; login refused (pending).
  - `get_or_create_user` override: after resolving, refuse login (return None) when user status is not active; update `last_login_at`; audit `auth.sso_login` / `auth.sso_linked` on first link.
- `accounts.backends.EmailBackend`: in `sso_only`, refuses everyone except `BREAK_GLASS_EMAIL`; break-glass login → audit `auth.break_glass_login`, `structlog` warning `break_glass_login`, `mail_admins` alert (ADMINS from env `DJANGO_ADMINS` "Name <email>,…", default empty); break-glass is MFA-required (Task 5 policy already covers it).
- Login view: in `sso_only`, the local form is not rendered and a POST returns the neutral error unless break-glass; a discreet "Emergency access" link (`?local=1`) shows the form for break-glass use.
- `manage.py disable_local_passwords [--dry-run]`: requires `AUTH_MODE == "sso_only"`; sets unusable passwords for all users except break-glass; prints counts; audits one `auth.local_passwords_disabled` event.
- Settings check: `AUTH_MODE` in SSO modes with missing OIDC endpoints/client → `ImproperlyConfigured` at startup.

**Acceptance Criteria:**
- Existing user linked by email on first SSO login; later, an IdP account with the same email but a different subject does **not** get access to that user (creates a pending user instead or is refused).
- Unknown user → pending account, login refused.
- Suspended/deactivated user → SSO login refused.
- `sso_only`: password login refused for normal users (even with correct password), allowed for break-glass and audited + alert email sent (`mail.outbox`).
- `local` mode: no OIDC URLs (`/oidc/authenticate/` → 404).
- Command disables local passwords except break-glass.
- Tests mock the IdP: call backend methods with claim dicts; no network.

- [ ] Steps 1–5. Commit `feat(accounts): OIDC sign-in modes with immutable identity linking and break-glass account`.

---

### Task 10: Developer experience, docs and final verification

**Files:**
- Create: `src/accounts/management/commands/create_dev_admin.py`
- Modify: `Makefile` (`dev-admin` target), `.env.example` (all new variables with dev-only values: `AUDIT_IP_HASH_KEY`, `SITE_URL`, `AUTH_MODE`, `BREAK_GLASS_EMAIL`, `DJANGO_ADMIN_PATH`, `NUM_PROXIES`, `DJANGO_ADMINS`, OIDC_* commented), `compose.yaml` (env for web/worker/beat; beat runs the schedule), `docs/development.md` (English; new env vars table rows, how to create the first admin and enroll MFA, how to test in French, audit DB role note), `README.md` (L1 features line) — coordinate: only after the translation of these files has landed on the branch.
- Test: `src/accounts/tests/test_dev_commands.py`

**Interfaces (Produces):**
- `manage.py create_dev_admin --email <e>`: refuses when `settings.DEBUG` is False; creates an active superuser with role `functional_admin`, prompts for a password (or `--password-from-env DEV_ADMIN_PASSWORD`), prints the next step (MFA enrollment at first login).

**Acceptance Criteria:**
- `create_dev_admin` refuses to run with DEBUG False (test with settings override).
- `make ci` steps (lint, test, i18n-check, security) pass locally; `docker compose up` brings web/worker/beat up; manual smoke: create admin, log in, enroll TOTP, create a user, open activation email in Mailpit, activate, switch to French.
- Coverage ≥ 85 %.

- [ ] Steps 1–5. Commit `chore(dev): dev admin command, env docs and L1 developer guide`.
