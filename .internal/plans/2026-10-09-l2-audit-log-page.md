# L2 — Read-only audit log page (bilingual EN/FR) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use beads-superpowers:subagent-driven-development (recommended) or beads-superpowers:executing-plans to implement this plan task-by-task. Steps within tasks use checkbox (`- [ ]`) syntax for human readability.

**Goal:** Auditors (and, within their scope, functional and technical administrators) can browse, filter and inspect the append-only audit log from a read-only page inside the application, in English and French, without going through the Django admin.

**Architecture:** Extends the existing `audit` app. Authorization in `audit/policies.py`, role-scoped reads in `audit/selectors.py`, thin GET-only views in `audit/views.py`, URLs mounted at `/audit/` under the `audit` namespace, templates in `audit/templates/audit/`. No model change, no migration. Server-rendered Django templates using the current minimal layout (`base.html`, `core/app.css`); the full L2 design system will restyle them later.

**Tech Stack:** Django 5.2 LTS, Python 3.13, uv, PostgreSQL 16, pytest/pytest-django.

**Source spec:** `docs/framing.md` / framing § 4.2 (Auditor: read-only on audit, no private content), § 4.4 matrix row "View the audit log" with footnote 6 (functional admin: functional events; technical admin: technical and security events; auditor: all), persona P9 ("filtered audit log"), and `.internal/specs/2026-10-09-mvp-knowledge-design.md` § 4 (`audit.AuditEvent`, "No modification view").

## Global Constraints

- Language of code, comments, docstrings, commit messages, docs: **English**. UI is bilingual EN/FR; every user-facing string goes through `gettext`/`gettext_lazy`/`{% translate %}`/`{% blocktranslate %}`. After adding strings run `cd src && uv run python ../manage.py makemessages -l fr --no-location --no-obsolete --ignore=.venv --settings=config.settings.test` and fill **every** new French entry in `src/locale/fr/LC_MESSAGES/django.po` (no untranslated, no fuzzy — `make i18n-check` enforces it). `.mo` files are not committed.
- The page is strictly **read-only**: GET (and HEAD) only, any other method → 405 (`require_safe`). No form on it writes anything. `AuditEvent` stays append-only; no model or migration change.
- Not allowed to see the audit log (anonymous, inactive, employee, community creator) → **404** (the page is not revealed), mirroring `accounts.views_manage.manage_required`. Responses are `never_cache`.
- Scope by role (framing footnote 6), as a union when a user holds several roles:
  - superuser or `auditor` → all events;
  - `technical_admin` → technical and security events: `action` starting with `auth.`;
  - `functional_admin` → functional events: every other action (`user.`, `users.`, `profile.` …).
  An event outside the viewer's scope is a 404 on the detail page, and never appears in the list or in the action filter choices.
- MFA is enforced by the existing `accounts.middleware.MFARequiredMiddleware` through `accounts.policies.requires_mfa`; the audit views do not re-implement it.
- No raw IP is ever stored or shown (only the existing `ip_hash`). `changes` is displayed as stored (sensitive keys were already stripped by `audit.services.record`). An event without actor shows "System"; an anonymized actor shows the model's own "Former employee" name.
- Lists use `select_related("actor")`, paginate 50 per page, newest first (`-created_at`, then `-pk`), and run a bounded number of queries (assert with `django_assert_max_num_queries`).
- Ruff (`make lint`), coverage gate 85 % (`make test`), Bandit `-ll` clean.
- Tests: pytest + pytest-django on real PostgreSQL (`config.settings.test`), in `src/audit/tests/test_*.py`. Existing fixtures/helpers in `src/conftest.py` and `src/accounts/tests/` may be reused; MFA-required users in HTTP tests need a verified OTP session (follow how existing `manage` view tests log in functional admins).
- Commits: conventional style (`feat(audit): …`), English, ending with the trailers
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_011UFaooKLLSUGTLxQZfHT7p`.

---

### Task 1: Audit visibility policy and role-scoped selectors

**Files:**
- Create: `src/audit/policies.py`, `src/audit/selectors.py`, `src/audit/tests/test_selectors.py`

**Interfaces (Produces):**
- `audit.policies.TECHNICAL_ACTION_PREFIXES = ("auth.",)`.
- `audit.policies.is_technical_action(action: str) -> bool`.
- `audit.policies.audit_scopes(user) -> frozenset[str]` with values among `"functional"`, `"technical"`: empty for anonymous or inactive users; both for superusers and auditors; `"technical"` for technical admins; `"functional"` for functional admins; union for multiple roles.
- `audit.policies.can_view_audit_log(user) -> bool` = `bool(audit_scopes(user))`.
- `audit.selectors.events_visible_to(user) -> QuerySet[AuditEvent]`: `AuditEvent.objects.none()` when no scope; all events for both scopes; otherwise filtered by `action__startswith` the technical prefixes (technical) or its negation (functional). `select_related("actor")`, ordered `-created_at, -pk`.
- `audit.selectors.filter_events(qs, *, action="", actor="", target="", date_from=None, date_to=None) -> QuerySet`: `action` exact match; `actor` case-insensitive substring on actor e-mail, first name or last name; `target` case-insensitive substring on `target_id` or exact `target_type`; `date_from`/`date_to` are `datetime.date` bounds, inclusive, interpreted in the current time zone (`date_to` includes the whole day). Blank values are ignored.
- `audit.selectors.action_choices(qs) -> list[str]`: sorted distinct actions present in `qs` (so the filter never reveals out-of-scope action codes).

**Acceptance Criteria:**
- Employee, community creator, anonymous, and an auditor whose account is suspended → `can_view_audit_log` False and `events_visible_to` empty.
- Auditor and superuser see `auth.*` and `user.*` events; technical admin sees only `auth.*`; functional admin sees only non-`auth.*`; a user with both admin roles sees all.
- Each filter narrows as specified; `date_to` includes events at 23:59 of that day in the active time zone; blank filters return the input unchanged.
- `action_choices` for a functional admin contains no `auth.` code.

- [ ] **Step 1:** Write failing tests for each criterion in `src/audit/tests/test_selectors.py`.
- [ ] **Step 2:** Run `uv run pytest src/audit -q` → FAIL.
- [ ] **Step 3:** Implement `policies.py` and `selectors.py`.
- [ ] **Step 4:** `make lint && make test` → PASS.
- [ ] **Step 5:** Commit `feat(audit): role-scoped audit log visibility and filters`.

---

### Task 2: Read-only audit log pages, navigation and translations

**Files:**
- Create: `src/audit/views.py`, `src/audit/urls.py`, `src/audit/forms.py`, `src/audit/templatetags/__init__.py`, `src/audit/templatetags/audit_tags.py`, `src/audit/templates/audit/event_list.html`, `src/audit/templates/audit/event_detail.html`, `src/audit/tests/test_views.py`
- Modify: `src/config/urls.py` (mount `path("audit/", include("audit.urls"))`), `src/core/templates/base.html` (header link), `src/locale/fr/LC_MESSAGES/django.po`, `README.md` (status paragraph mentions the audit log page), `docs/development.md` (short "Audit log" note: URL, who sees what)

**Interfaces (Consumes):** everything Task 1 produces.

**Interfaces (Produces):**
- URL namespace `audit`: `audit:event_list` → `/audit/`, `audit:event_detail` → `/audit/<int:pk>/`.
- `audit.forms.AuditFilterForm(forms.Form)`: `action` (`ChoiceField`, choices = blank "All actions" + `action_choices(scope_qs)`, passed in by the view), `actor` (`CharField`, optional, max 100), `target` (`CharField`, optional, max 100), `date_from` / `date_to` (`DateField`, optional, `type="date"` widget); `clean()` rejects `date_from > date_to` with a translated error. **Invalid form → error summary shown and the unfiltered scope list** (simple, predictable).
- `audit.views.event_list(request)`: `require_safe`, `never_cache`, 404 unless `can_view_audit_log`; builds the form from `request.GET`, filters `events_visible_to(request.user)`, paginates 50 (`Paginator.get_page`), context `page_obj`, `form`, `querystring` (current filters without `page`, url-encoded, for pagination links).
- `audit.views.event_detail(request, pk)`: same guards; `get_object_or_404(events_visible_to(request.user), pk=pk)`; shows date/time (localized, with time zone), action, category (Functional / Technical and security), actor (name + e-mail, or "System"), target type and id, community id if any, request id, IP hash, and `changes` rendered as pretty-printed JSON inside `<pre>` (auto-escaped). Back link to the list.
- `audit.templatetags.audit_tags`: filter `can_view_audit_log(user)`; filter `action_category(action) -> str` (translated "Functional" / "Technical and security").
- `base.html`: an "Audit log" (FR « Journal d'audit ») header link to `audit:event_list` shown only when `user|can_view_audit_log`.

**Page requirements:**
- List: one `h1` "Audit log"; filter form (`role="search"`, every field labelled, errors linked with `aria-describedby`); a count line ("N event(s)", pluralized); a table with `scope="col"` headers: Date, Action, Category, Actor, Target; each row links to its detail; empty state text "No audit event matches these filters."; pagination like `manage/user_list.html` but carrying all filters.
- No edit, delete, export or bulk action anywhere.

**Acceptance Criteria:**
- Anonymous, employee, community creator → 404 on both URLs (HTTP tests). Auditor → 200. Functional admin / technical admin with a verified MFA session → 200 with only their scope; functional admin gets 404 on an `auth.*` event detail, technical admin on a `user.*` one.
- POST, PUT, DELETE on both URLs by an auditor → 405; `AuditEvent` count unchanged.
- Filters via query string narrow the list; pagination links keep the filters; `date_from > date_to` shows the translated error.
- The header link appears for an auditor and not for an employee.
- Detail page shows "System" for a null actor and escapes HTML in `changes` (a `<script>` value is rendered escaped).
- List with 60 events runs a bounded number of queries (`django_assert_max_num_queries`), and page 2 shows the remaining 10.
- Page renders in French with `Accept-Language: fr` ("Journal d'audit").
- `make lint`, `make test`, `make i18n-check` pass.

- [ ] **Step 1:** Write failing HTTP tests in `src/audit/tests/test_views.py`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement form, views, URLs, templates, template tags, header link.
- [ ] **Step 4:** Extract messages, translate every new French entry, update README and development guide.
- [ ] **Step 5:** `make lint && make test && make i18n-check` → PASS.
- [ ] **Step 6:** Commit `feat(audit): read-only audit log pages for auditors and administrators`.

---

### Task 3: Second factor for auditors

> **Dropped (owner decision, 2026-10-09):** auditors keep signing in without a second factor. Kept here for the record.

The audit log exposes every account's e-mail, sign-in failures and security events; auditors are the only role reading all of it without a second factor today.

**Files:**
- Modify: `src/accounts/policies.py` (`requires_mfa`), `src/accounts/tests/` (the existing MFA policy tests), `docs/development.md` and `README.md` where MFA roles are listed

**Interfaces (Produces):**
- `accounts.policies.requires_mfa(user)` also returns True for holders of the `auditor` role. `PRIVILEGED_ROLES` is **not** changed (it carries other meaning).

**Acceptance Criteria:**
- An active auditor without a confirmed device is redirected to MFA setup on `/audit/`; with a verified session gets 200.
- Employees are still not asked for MFA.
- Task 2's auditor HTTP tests use a verified MFA session.
- `make lint`, `make test`, `make i18n-check` pass.

- [ ] **Step 1:** Write failing tests.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement and update docs.
- [ ] **Step 4:** `make lint && make test && make i18n-check` → PASS.
- [ ] **Step 5:** Commit `feat(accounts): require a second factor for auditors`.
