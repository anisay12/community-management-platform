# REST API foundation (DRF) — design

> Status: **approved, stress-tested** · Date: 2026-10-16
> Implements: MVP spec § 15 (`2026-10-09-mvp-knowledge-design.md`) for the apps already built
> (communities, posts, documents). Sub-project **A**. Sub-project **B** (L6, REX and articles) is
> specified separately and builds its endpoints on this foundation.

## 1. Decisions

| # | Decision | Rationale |
|---|---|---|
| D-A1 | Django REST Framework + `drf-spectacular` (+ `drf-spectacular-sidecar`) | Spec § 15 names DRF; spectacular gives OpenAPI at `/api/schema/` and `/api/docs/` (spec L11) with self-hosted assets (strict CSP, no CDN). |
| D-A2 | **Two front doors over one domain layer.** The UI stays server-rendered Django views + HTMX (no-JS fallback kept, spec L2). DRF exposes the same `policies` / `selectors` / `services` as JSON. JS widgets may call the API. | No rewrite of L1–L5 pages; business rules, audit, notifications and transactions stay in one place. |
| D-A3 | **Session authentication only, same origin, no CORS.** | Exactly spec § 15. Machine/integration tokens are deferred to roadmap R4 with their own ADR. |
| D-A4 | Plumbing in a new `src/api/` app; endpoints in `<app>/api/` packages owned by each domain app. | Matches the modular monolith: domain code stays in its app, `api` imports no domain internals. |
| D-A5 | Member-facing scope: read + write for daily actions. Platform governance (suspend/archive communities, categories, creation requests, admin access grants) and report **decisions** stay UI-only. | Spec § 15: "without duplicating the whole UI". |

## 2. Plumbing (`src/api/`)

### Settings (`config/settings/base.py`)

```python
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["api.permissions.IsActiveVerifiedUser"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],  # + Browsable in dev.py
    "DEFAULT_PARSER_CLASSES": [JSONParser, MultiPartParser, FormParser],
    "DEFAULT_PAGINATION_CLASS": "api.pagination.PagePagination",
    "DEFAULT_THROTTLE_CLASSES": ["api.throttling.ReadThrottle", "api.throttling.WriteThrottle"],
    # Coarse anti-abuse guard only; precise per-action limits stay in posts.ratelimit.
    "DEFAULT_THROTTLE_RATES": {"api_read": "3600/hour", "api_write": "1000/hour"},  # env-overridable
    "EXCEPTION_HANDLER": "api.exceptions.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}
```

`config/settings/dev.py` adds `BrowsableAPIRenderer`. The test settings keep the throttles on, using the in-memory cache.

### Modules

| Module | Responsibility |
|---|---|
| `api/permissions.py` | `IsActiveVerifiedUser`: authenticated **and** (`not accounts.policies.requires_mfa(user)` or `user.is_verified()`). DRF's session authentication treats an inactive account (pending, suspended, deactivated) as anonymous, so unauthenticated and inactive both answer 401 `not_authenticated`; MFA missing → 403 `mfa_required`. Never redirects. |
| `api/exceptions.py` | Uniform error body (§ 4) and the mapping `DomainError` code → HTTP status (§ 4). |
| `api/pagination.py` | `PagePagination` (numbered, `page_size=20`, `max_page_size=50`, `?page_size=`), and `FeedCursorPagination`, which wraps `posts.selectors.encode_cursor` / `decode_cursor` and the `FeedPage` returned by `community_feed` / `home_feed` (`{"results", "next_cursor"}`). |
| `api/throttling.py` | `ReadThrottle` (safe methods), `WriteThrottle` (unsafe methods), scoped per user: a coarse anti-abuse guard set well above the business limits in `posts.ratelimit`, which stay the precise per-action rule shared with the UI. A test asserts that `api_write` ≥ the sum of `POSTS_RATE_LIMITS`. |
| `api/idempotency.py` | `run_idempotent(request, scope, operation)`: reads the `Idempotency-Key` header (must be a UUID) and calls `core.idempotency.run_once`, storing `{status, kind, public_id, fingerprint}`. `scope` uses **the same strings as the UI views** (for example `documents:create:{community.pk}`), so one key can never run a write twice through either door. `fingerprint` = SHA-256 of the scope + canonical JSON of the non-file fields + each uploaded file's name and size; a replay with a different fingerprint answers **409 `idempotency_key_reused`**. A matching replay re-reads the object through its selector, serializes its **current** state with the **original** status code, and sets `Idempotent-Replayed: true`. The key is **required** (400 `idempotency_key_required` when absent) on join, document upload and new version, and optional elsewhere. `core.idempotency` stays backward compatible for the UI. |
| `api/mixins.py` | `VisibleObjectMixin.get_object()` resolves through the app selector (`get_visible_post_or_404`, `get_visible_document_or_404`, the community lookup used by `communities.views`) so hidden objects answer 404. `check_policy(predicate, *args)` raises 403. |
| `api/serializers.py` | Shared `UserSummarySerializer` (`public_id`, `display_name`, `avatar_url`; respects `profile_visibility`), `TagSerializer`. |
| `api/views.py` | `GET /me/` (my profile summary, global roles, `requires_mfa`, language, time zone); `GET /tags/?q=` (prefix, ≥ 2 chars, 8 results). |
| `api/urls.py` | `/api/v1/` includes `communities.api.urls`, `posts.api.urls`, `documents.api.urls` and its own views; `/api/schema/` and `/api/docs/` require `IsActiveVerifiedUser`. Included from `config/urls.py`. |

### Middleware

`accounts.middleware.MFARequiredMiddleware.process_view` skips its redirect only when **both**
conditions hold: the path starts with `/api/`, and the resolved view is a DRF view
(`getattr(view_func, "cls", None)` is a subclass of `rest_framework.views.APIView`). A plain
Django view mounted under `/api/` keeps the normal MFA redirect. MFA is then enforced by
`IsActiveVerifiedUser`, which is the default permission on every API view (the schema and docs
pages included). A guard test walks the URL resolver and asserts that every pattern under
`/api/` is a DRF view whose permission classes include `IsActiveVerifiedUser`. This moves the
control without weakening it.

### Per-app packages

`communities/api/`, `posts/api/`, `documents/api/`: `serializers.py`, `views.py`, `urls.py`,
`tests/`. These rules apply:

- Views are thin: an input serializer validates, a policy check runs, then one selector or
  service call, then an output serializer renders the result.
- **Serializers never save.** No `ModelSerializer.create/update`; writes call the service
  functions (keyword-only, actor first), so audit, notifications and transactions are shared with
  the UI.
- Explicit `fields = [...]`, never `__all__`. Internal `id` is never exposed; only `public_id`,
  community `slug`, version `number`.
- Markdown fields come back as `body` (source) plus `body_html` (the sanitised render already
  stored). Input is Markdown only and goes through the service, which uses `core.markdown`.
- A `permissions` block (`can_edit`, `can_comment`…) appears **only on detail** endpoints. List
  items carry data only. List views call the same priming helpers as the UI views
  (`prime_membership_cache`, `mark_share_access`, `select_related` / `prefetch_related` of
  author, tags and community) to avoid N+1 queries.

## 3. Endpoints (`/api/v1/`)

Every list is paginated. ★ = `Idempotency-Key` required.

### Communities (`communities/api/`)

| Method & path | Service / selector | Notes |
|---|---|---|
| `GET /communities/` | `selectors.visible_communities` | filters `category`, `access_mode`, `mine`, `q` (same `SearchQuery` as the catalogue), `ordering` ∈ activity, members, name |
| `GET /communities/{slug}/` | visible lookup + `can_view_metadata` / `can_view_full_metadata` | invite-only unlisted → 404; adds `my_membership` (role, notification level) and `my_request` |
| `GET /communities/{slug}/members/` | as the UI members tab | requires the same visibility as the UI |
| `POST /communities/{slug}/join/` ★ | `services.join` | body `{message?}`; 201 membership or 202 request |
| `POST /communities/{slug}/leave/` | `services.leave` | `last_owner` → 409 |
| `DELETE /membership-requests/{public_id}/` | `services.cancel_request` | own request only |
| `GET /invitations/` | my pending invitations | |
| `POST /invitations/{public_id}/accept/` · `/decline/` | `accept_invitation` / `decline_invitation` | |
| `GET /communities/{slug}/requests/` | animator+ (`can_manage_members`) | |
| `POST /communities/{slug}/requests/{public_id}/accept/` · `/reject/` | `accept_request` / `reject_request` | reject body `{note?}` |
| `GET /communities/{slug}/invitations/` | pending invitations of the community | animator+ |
| `POST /communities/{slug}/invitations/` | `services.invite` | body `{email, role}`, looked up exactly like `InvitationForm.clean_email`; **always 202 with the same neutral body**, whether the address is unknown, inactive or valid (no user enumeration); no invitation id in the response |
| `DELETE /communities/{slug}/invitations/{public_id}/` | `revoke_invitation` | |
| `PATCH /communities/{slug}/members/{user_public_id}/` | `services.change_role` | body `{role}` |
| `DELETE /communities/{slug}/members/{user_public_id}/` | `services.remove_member` | |

### Posts (`posts/api/`)

| Method & path | Service / selector | Notes |
|---|---|---|
| `GET /feed/` | `selectors.home_feed` | cursor |
| `GET /communities/{slug}/posts/` | `selectors.community_feed` | cursor; `kind`, `unanswered` |
| `POST /communities/{slug}/posts/` | `create_post` | body `{kind, title, body, tags[], publish}` |
| `GET /posts/{public_id}/` | `get_visible_post_or_404` | includes `version`, permissions block (`can_edit`, `can_comment`…) |
| `PATCH /posts/{public_id}/` | `update_post` / `set_tags` | `version` required → `edit_conflict` 409 |
| `DELETE /posts/{public_id}/` | `delete_draft` (own draft) else `archive_post` | |
| `POST /posts/{id}/publish/` | `publish_draft` | |
| `POST /posts/{id}/pin/` · `/unpin/` | `pin_post` / `unpin_post` | `pin_limit` → 409 |
| `POST /posts/{id}/accept-answer/` · `DELETE …/accept-answer/` | `accept_answer` / `clear_accepted_answer` | body `{comment}` |
| `POST /posts/{id}/share/` | `share_post` | body `{community, title, comment?}` |
| `POST /posts/{id}/hide/` · `/unhide/` · `/approve/` · `/reject/` | moderation services | hide/reject need `reason` / `note` |
| `GET /posts/{id}/revisions/` | `can_view_revisions` | |
| `GET /posts/{id}/comments/` · `POST` | `comment_thread` / `add_comment` | body `{body, parent?}`; `depth_exceeded` → 400 |
| `PATCH /comments/{public_id}/` | `update_comment` | |
| `POST /comments/{id}/hide/` · `/unhide/` | `hide_comment` / `unhide_comment` | |
| `PUT` · `DELETE /posts/{id}/reactions/{kind}/` (and `/comments/{id}/…`) | `set_reaction(present=…)` | idempotent; returns `{kind, present, counts}`. `counts` comes from a helper shared with the HTMX view, extracted from `posts/views_interactions.py` (the stored deferred counts adjusted by this change), and is documented as approximate until the recount job runs (spec § 2) |
| `PUT` · `DELETE /posts/{id}/bookmark/` | `set_bookmark` | body `{collection?}` |
| `GET` · `POST /bookmark-collections/`, `PATCH` · `DELETE /bookmark-collections/{id}/` | collection services | |
| `GET /bookmarks/` | bookmarks page selector | |
| `POST /posts/{id}/reports/` · `/comments/{id}/reports/` | `report` | `already_reported` → 409 |

### Documents (`documents/api/`)

| Method & path | Service / selector | Notes |
|---|---|---|
| `GET /communities/{slug}/documents/` | resources tab selector | filters `doc_type`, `tag`, `status` (moderators) |
| `GET /documents/{public_id}/` | `get_visible_document_or_404` | versions with `scan_status`; `download_url` / `preview_url` = existing UI routes (X-Accel), only when `can_download` |
| `POST /communities/{slug}/documents/` ★ | `create_document` | multipart: `file` + metadata; 201 with `scan_status=pending` |
| `PATCH /documents/{id}/` | `update_document` | |
| `POST /documents/{id}/versions/` ★ | `add_version` | multipart |
| `POST /documents/{id}/versions/{number}/set-reference/` | `set_reference_version` | |
| `POST /documents/{id}/archive/` · `/restore/` | `archive_document` / `restore_document` | |
| `POST /documents/{id}/links/` · `DELETE /documents/{id}/links/{link_id}/` | `link_document` / `unlink_document` | body `{post}` |
| `PUT` · `DELETE /documents/{id}/bookmark/` | `set_document_bookmark` | |
| `POST /documents/{id}/reports/` | `report_document` | |

File bytes never travel through the JSON API. Downloads keep going through the existing
`documents:download` / `documents:preview` routes, which re-check `can_download`, write the
`DownloadLog` and answer with `X-Accel-Redirect` (ADR-0001).

### Shared

`GET /me/`, `GET /tags/?q=`, `GET /api/schema/`, `GET /api/docs/`.

Where a `link_id` integer exists today (`DocumentLink`), the API exposes it unchanged, as the UI
URL already does.

## 4. Errors

Body: `{"code": str, "detail": str, "fields": {name: [str]}?, "request_id": str}`. `detail` is
translated (`LocaleMiddleware` runs before the API, using the user's saved language or
`Accept-Language`).

| Source | Status |
|---|---|
| Serializer `ValidationError` | 400 `invalid`, with `fields` |
| `DomainError` codes `forbidden`, `forbidden_role`, `forbidden_target`, `invite_only`, `kind_forbidden`, `not_member`, `own_content`, `read_only`, `self_action`, `tag_creation_forbidden` | 403 |
| `DomainError` codes `already_member`, `already_reported`, `edit_conflict`, `expired`, `idempotency_key_reused`, `invalid_state`, `last_owner`, `name_taken`, `pin_limit`, `slug_taken`, `submission_in_progress`, `version_label_taken` | 409 |
| `DomainError("storage_unavailable")` | 503 (spec § 16) |
| `DomainError("rate_limited")` and DRF `Throttled` | 429 with a `Retry-After` header (from `error.retry_after` / the throttle wait) |
| Any other `DomainError` | 400 |
| `Http404` / `NotFound` | 404 `not_found` (same body whatever the reason, so nothing is revealed) |
| `PermissionDenied` | 403 `permission_denied` (DRF's code; `forbidden` is the `DomainError` code) |
| Django `BadRequest` / `SuspiciousOperation` | 400 `bad_request` (a `SuspiciousOperation` keeps Django's `django.security.*` log) |
| Unhandled | 500 `server_error`, only the `request_id` (logged with `structlog`) |

The code → status map lives in `api/exceptions.py` as one dict, and a test fails when a
`DomainError` code used in `src/` is missing from it. The test collects the codes with Python's
`ast` module: the first string argument of every `DomainError(...)` call, including calls that
span several lines. A new code therefore has to be classified deliberately, and it defaults to
400 only when it is listed explicitly.

## 5. Security checklist

- **No credentials through the API.** No endpoint under `/api/` accepts a password, an OTP or a
  token. Login, logout, password reset, MFA and account management stay in the UI, so axes,
  MFA and `sso_only` keep protecting them. `DEFAULT_AUTHENTICATION_CLASSES` is
  `SessionAuthentication` only; a settings test fails if any other class appears. Token auth
  (R4) requires a new ADR.
- CSRF enforced on unsafe methods (`SessionAuthentication`); JS sends `X-CSRFToken` like HTMX.
- No CORS middleware or package; a test asserts that no `Access-Control-Allow-Origin` header is
  sent.
- 404 for hidden, 403 for forbidden: a parametrized test checks that UI and API give the same
  answer for the same object and user.
- MFA and account status enforced by the default permission; a test asserts that no view sets
  `permission_classes` without including `IsActiveVerifiedUser`.
- Uploads go through `documents.validation` → quarantine → ClamAV, exactly like the UI;
  `DOCUMENT_MAX_UPLOAD_BYTES` applies; nginx `client_max_body_size` covers `/api/`.
- Markdown only, rendered by `core.markdown`; raw HTML is never accepted.
- Explicit serializer fields; user data filtered by `profile_visibility`.
- Rate limits: the business limits in the services plus the DRF per-user throttles.
- `/api/docs/` uses `SpectacularSwaggerSplitView` with the sidecar's static assets: no CDN and
  no inline script, so it stays compatible with the strict global CSP that L11 has to add. Today
  the site has no global CSP; nginx sets one only on protected files.
- No new personal data, so `accounts/privacy.py` export and erasure are unchanged.

## 6. Testing

- `src/api/tests/`: permission class (anonymous 401, pending/suspended 401, privileged without
  MFA 403 `mfa_required`, verified 200), the exception map (every status in § 4, with
  `Retry-After`), CSRF rejection, absence of CORS headers, schema and docs require login, an
  idempotency replay that returns the same body with `Idempotent-Replayed`, the DomainError code
  coverage test, and the default-permission guard test.
- `<app>/api/tests/`: the permission matrix per endpoint (role × access mode × object state:
  anonymous 401, hidden 404, forbidden 403, allowed 2xx), the audit event written on each write,
  notification rows where the service notifies, UI/API status parity, and query-count tests on
  list endpoints asserting the **same number of queries** for a page of 2 and a page of 20 items.
- **UI/API parity matrix** (`src/api/tests/test_parity.py`). Fixture world: communities in the
  3 access modes; posts published / draft / pending_review / hidden / archived; documents
  active / restricted / archived / expired / scan-pending / infected. Roles: anonymous,
  non-member, member, moderator, functional admin without and with an `AdminAccessGrant`,
  auditor. For every (object, role) pair, the UI detail URL and the API detail URL return the
  same class of answer (2xx / 403 / 404), and every list endpoint returns the same set of
  `public_id`s as the UI selector.
- Guard test: `<app>/api/views.py` never uses `.objects.`. API views get objects only through
  the app's selectors.
- Documents: multipart upload creates a `pending` version and the scan task is called (eager);
  an infected scan is never downloadable; `download_url` is absent without `can_download`.
- `make lint` adds `manage.py spectacular --validate --fail-on-warn --file /dev/null`.
- Coverage gate ≥ 85 % kept.

## 7. Delivery slices

Each slice is a branch merged to `main` with green gates.

1. **A1, plumbing:** dependencies, settings, `src/api/`, the middleware exemption, `/me/`,
   `/tags/`, schema and docs, plumbing tests.
2. **A2, communities** endpoints + tests.
3. **A3, posts** endpoints + tests.
4. **A4, documents** endpoints + tests.
5. **A5, docs:** `docs/development.md` § API, `AGENTS.md` (stack, repository map, URL map, the
   § 9 checklist gains "API endpoint + tests"), the delivery status table, and
   `ADR-0003-session-only-same-origin-api.md` + `docs/decisions/INDEX.md`.

## 8. Binding constraint for later packages

`core.middleware.ActivityMiddleware` treats every authenticated request as activity: it slides
the 8 h inactivity expiry and updates `last_seen_at`, which feeds the L10 indicators. Any
**timer-driven** request (polling such as the L8 bell, or a heartbeat) must send
`X-Background-Request: 1`, and `ActivityMiddleware` must neither slide the session nor update
`last_seen_at` for such requests. Otherwise a forgotten tab keeps its session alive forever and
counts as daily activity. Sub-project A has no timer-driven endpoint. **L8 implements the
exemption, with a test**, when it adds the first poller. L6 auto-save is driven by the user
typing, so it counts as activity.

## 9. Out of scope

Token or OAuth authentication (R4), CORS, API endpoints for events, notifications and search
(they come with L7, L8 and L9), a people-lookup endpoint (L9, respecting `is_discoverable`),
platform governance and report-decision endpoints, API versioning beyond the `/v1/` prefix, a
JS client SDK, and a site-wide Content-Security-Policy (an L11 hardening item; spec § 17).

## Stress Test Results: REST API foundation

### Resolved Decisions
- MFA exemption: scoped to DRF views under `/api/`, not to the path prefix, with a guard test
  that walks the URL resolver.
- Invitations: `{email, role}` with a neutral 202, mirroring `InvitationForm`, so no user
  enumeration.
- Throttles: coarse anti-abuse levels (write 1000/h, read 3600/h); the business limits stay in
  the services; a test checks write ≥ the sum of the business limits.
- Reaction counts: a helper shared with the HTMX view, documented as approximate until the
  recount job runs.
- Idempotency: a replay returns the current state with the original status; a fingerprint
  mismatch answers 409 `idempotency_key_reused`; the API shares the UI's scope strings.
- Auth surface: no credential-accepting endpoint, `SessionAuthentication` only (settings test),
  token auth only through a new ADR.
- Query cost: `permissions` block on detail only; lists reuse the UI's priming helpers; tests
  assert a constant query count.
- Background requests: a binding constraint for L8 (no session slide, no activity update).
- Parity: a fixed object-state × role matrix, plus the "no `.objects.` in `api/views.py`"
  guard.
- Docs page: `SpectacularSwaggerSplitView`, no inline script.

### Changes Made
- §§ 2, 3, 5, 6 updated; § 8 (binding constraint) added; out-of-scope list extended.

### Deferred / Parking Lot
- `X-Background-Request` exemption in `ActivityMiddleware`: L8.
- People lookup endpoint: L9.
- Site-wide CSP: L11.
- Token / integration authentication: R4, with its own ADR.

### Confidence Assessment
- Overall: **High.** The API is a thin layer over services that already enforce policies,
  audit and transactions.
- Areas of concern: keeping read-side visibility identical between the two front doors (the
  parity matrix mitigates it); the size of A3 (the posts endpoints), which may need splitting
  during planning.
