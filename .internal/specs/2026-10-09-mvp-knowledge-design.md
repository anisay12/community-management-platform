# MVP Specification "Communities & knowledge" — TALAN Communities Platform

> Status: **to be reviewed** · Date: 2026-10-09
> Source: [framing document](../../docs/framing.md) (§ 9.1, work packages L0 to L11).
> Validated decision: "Knowledge first" scope (training, skills and quizzes in release 2).
> Decisions still open, applied here with their default value: D1 agnostic hosting, D2 local auth + OIDC ready, D4 neutral token-based theme, D5 decided: bilingual EN (default) + FR, D6 aggregated manager access (out of the MVP anyway), D8 default retention periods.
> Project language: everything in the project is written in English (owner's decision, 2026-10-09); the application itself is bilingual (English default + French).

---

## 1. Objective and non-goals

**Objective**: an employee can join communities, read and publish content there (discussions, questions, announcements, articles, REX), securely upload and download documents, register for events, be notified and find everything through search. A lead runs and measures their community. An administrator governs the platform.

**Out of the MVP (explicitly)**: training, paths, skills, quizzes/mock tests, certifications, mentoring, manager dashboard, SSO in production, Teams/corporate calendar, AI, WebSocket real time, recurring events, attendance sheets, PDF exports.

The MVP data model **reserves** extension points (e.g. `Event.kind = training`, shared `Tag`) without creating empty tables.

---

## 2. Cross-cutting conventions

| Topic | Rule |
|---|---|
| Identifiers | Internal bigint `id`; unique UUIDv4 `public_id`, the only identifier exposed in URLs and the API; readable `slug` for communities |
| Timestamps | `created_at`, `updated_at` (UTC) on all business tables; displayed in the user's time zone (`UserProfile.timezone`, default `Europe/Paris`) |
| Archiving | Nullable `archived_at`; selectors exclude archived items by default; no `CASCADE` deletion of business content from a community (`on_delete=PROTECT`) |
| Access layer | `selectors.py` (reads, always through `visible_to(user)`), `services.py` (transactional writes), `policies.py` (`can_<action>(user, obj) -> bool`) |
| Business errors | `DomainError(code, message)` exception translated into a user message (toast) or an API 400/409 |
| Unauthorized private object | **404** response (does not reveal existence); forbidden action on a visible object: **403** |
| Rich text | Restricted Markdown (headings, lists, links, code, quotes, tables), rendered server-side then sanitized by `nh3`; no raw HTML |
| Pagination | Cursor (`?cursor=`) for feeds; numbered for catalogues and search results (size 20, max 50) |
| Audit | Every action listed in § 11 creates an `AuditEvent` in the same transaction |
| Denormalized counters | **Exact and transactional** (`F()` in the business transaction): `member_count`, `registered_count`. **Deferred** (batched Celery task, deduplicated per target through a Redis lock, a delay of a few seconds accepted): `comment_count`, `reaction_counts`, `download_count`. Nightly task to verify/correct all counters |
| Idempotence of sensitive forms | Hidden `idempotency_key` field (UUID) on membership, event registration/unregistration, submission for review, document upload; key remembered 24 h in Redis with the result; a double submit returns the same result without reprocessing |
| HTMX and CSRF | CSRF token sent through the `X-CSRFToken` header (`hx-headers` attribute on `<body>`); no modifying request over GET |
| Links in Markdown | Allowed schemes: `https`, `http`, `mailto`; external links with `rel="noopener noreferrer nofollow"`; external images not loaded (only images stored in the platform) |
| i18n | All strings go through gettext; English (default) and French enabled; French catalogue complete (checked in CI) |

---

## 3. Work package L0 — Technical foundation

**Deliverables**: `src/config` project (`base/dev/test/prod` settings read from the environment via `django-environ`), `compose.yaml` (web, worker, beat, postgres 16, redis 7, SeaweedFS S3-compatible storage (MinIO is no longer distributed on Docker Hub), clamav, mailpit), multi-stage non-root Dockerfile, `uv.lock`, `.env.example`, GitHub Actions CI (or a `make ci` script runnable locally as long as GitHub is not connected).

**CI checks**: `ruff check`, `ruff format --check`, `pytest` (with a real PostgreSQL as a service), `python manage.py makemigrations --check`, migrations from an empty database, `pip-audit`, `bandit -q`, `gitleaks`, image build, `trivy image` (blocking on CRITICAL).

**Observability**: `X-Request-ID` middleware (generates or propagates, added to logs and to the response); JSON logs (`structlog`); `/healthz` (process alive) and `/readyz` (database + Redis reachable); `/metrics` (`django-prometheus`) protected by network/token.

**Acceptance**: `docker compose up` then `make demo` makes the application usable on `http://localhost:8000`; CI green; gitleaks `grep` with no leak.

---

## 4. Work package L1 — Accounts, roles, audit

### Models

- `accounts.User(AbstractBaseUser, PermissionsMixin)`: `email` (unique, case-insensitive comparison through a `Lower(email)` constraint), `first_name`, `last_name`, `status` ∈ {`pending`, `active`, `suspended`, `deactivated`}, `is_staff`, `last_seen_at` (updated at most once / 5 min), `public_id`.
- `accounts.UserProfile` (1–1): `job_title`, `bio` (≤ 2,000 chars), `avatar` (object storage, optional), `timezone`, `interests` (M2M `Tag`), `profile_visibility` ∈ {`private`, `communities`, `company`} (default `company` for name/job title/communities; bio and interests according to the choice), `is_discoverable` (default true). Avatar upload is delivered in L5 (needs the antivirus scan pipeline and private file serving); L1 defines the field and shows initials.
- `organizations.OrganizationUnit`: `name`, unique `code`, `parent` (nullable FK, tree).
- `organizations.Employment` (1–1 User): `unit`, `manager` (nullable FK User, ≠ self, CHECK).
- Global roles = Django `Group`: `employee` (FR: collaborateur), `community_creator` (FR: créateur de communautés), `functional_admin` (FR: admin fonctionnel), `technical_admin` (FR: admin technique), `auditor` (FR: auditeur). The "manager" status is **derived** from `Employment.manager`.
- `audit.AuditEvent`: `actor` (nullable FK, `SET_NULL`), `action` (code), `target_type`, `target_id`, `community` (nullable FK), `changes` (before/after JSON restricted to non-sensitive fields), `ip_hash` (HMAC of the IP, not the raw IP), `request_id`, `created_at`. No modification view; application DB role without `DELETE` on this table in prod (documented).

### Authentication

- E-mail + password login; `argon2`; validators: length ≥ 12, similarity, common passwords; `django-axes`: 5 failures → 15 min lock per (e-mail, IP), neutral message ("Invalid credentials" (FR: « identifiants invalides »)).
- Reset by e-mail (Django token, 1 h), identical response whether or not the account exists.
- TOTP MFA (`django-otp`) mandatory for `functional_admin`, `technical_admin`, `is_staff`; the Django admin is served on a configurable path and requires MFA.
- Sessions: `cached_db` storage, 8 h inactivity expiry, `SESSION_COOKIE_SECURE`, `HttpOnly`, `SameSite=Lax`, rotation at login; suspension → deletion of the user's sessions.
- OIDC: `mozilla-django-oidc` integrated, driven by the `AUTH_MODE` setting ∈ {`local` (MVP default), `mixed` (transition: SSO offered, password still accepted), `sso_only`}.
  - `ExternalIdentity` model (`user`, `provider`, immutable `subject` — `oid`/`sub` claim —, unique (`provider`, `subject`)). The e-mail is used only for the **first linking** of an existing account; after that only `subject` is authoritative (a reassigned e-mail does not give access to the former account). Unknown user → `pending` account.
  - In `sso_only`: local passwords unusable (`set_unusable_password` through a migration command), local form hidden and rejected server-side, **except one designated emergency account** ("break glass"), MFA mandatory, every login audited and alerted.
  - Deactivation aligned with the IdP: checked at every login and nightly synchronization (R4); an account deactivated in the IdP loses its sessions.
- Account creation at the MVP: by the functional admin (form + controlled CSV import: e-mail, first name, last name, unit, manager e-mail), activation e-mail (72 h link). No free sign-up.

### Account lifecycle and GDPR

- **Departure** (`deactivated` status): sessions deleted, account cannot log in; name kept on contributions (company's legitimate interest, **to be confirmed with the DPO**); removed from member lists, suggestions and people search.
- **Anonymization** (on an accepted erasure request, or automatically 3 years after deactivation through a scheduled task): deletion of the profile, avatar, interests, bookmarks, preferences, notifications, `UserDailyActivity` and `DownloadLog`; e-mail replaced by a unique technical value; `author_display` replaced by "Former employee" (FR: « Ancien collaborateur ») on all content; mentions rewritten; content kept. `AuditEvent` rows keep only the technical identifier until their purge (1 year).
- **Export** of one's own data (right of access): JSON archive generated by a Celery task, downloadable by the user alone for 7 days.
- An employee can delete (archive) their own content at any time.

### Pages

Login, forgotten password, activation, profile (read/edit), preferences, 403/404/429/500 error pages in the application's design (the 500 is static, with no database dependency).

### Acceptance (tests)

`pending` or `suspended` account → redirect to login with a message; employee → 404 on the admin; brute force → lock; role change → `AuditEvent`; CSV import: invalid rows reported without partial creation (transaction).

---

## 5. Work package L2 — Design system and navigation

- Bootstrap 5.3 compiled with Sass variables overridden by **tokens** (`--tl-color-primary`, `--tl-font-sans`…) in `core/static/core/tokens.scss`; a single file to modify when the charter is received. Logo: `brand/logo.svg` placeholder with a text placeholder "Communities" (FR: « Communautés ») (no Talan logo invented).
- Light mode by default; dark mode via `prefers-color-scheme` (dedicated tokens).
- Components as included templates (`{% include "components/card.html" with … %}`): content card, community card, empty state (illustration + action), alert, toast (aria-live), accessible modal, pagination, role badge, avatar, breadcrumb, tabs, form (label, help, error linked by `aria-describedby`).
- MVP main navigation: Home (FR: Accueil), Communities (FR: Communautés), Resources (FR: Ressources), Events (FR: Événements), Lessons learned (FR: Retours d'expérience), Search (field) (FR: Recherche), Notifications (bell + counter), Profile (menu) (FR: Profil), Administration (if authorized). The Training (FR: Formations), Skills (FR: Compétences), Mock tests (FR: Tests blancs) entries are **not displayed** at the MVP (no fake empty screen).
- Mobile: top bar + side menu (offcanvas); touch targets ≥ 44 px.
- HTMX: reactions, subscription, catalogue filters, feed loading, notification marking; every HTMX interaction has a working no-JS fallback for the main actions (classic POST form).
- Accessibility: one `h1` per page, landmarks, skip link, visible focus, AA contrast; `axe-core` through Playwright in CI on the main pages (blocking on "serious"/"critical" violations).

---

## 6. Work package L3 — Communities

### Models

- `CommunityCategory`: unique `name`, `slug`, `description`, `icon` (Bootstrap Icons icon name), `order`, `is_active`. Initial data: the 12 categories of the prompt (data migration, editable afterwards).
- `Community`: `name` (unique among non-archived), unique `slug`, `category` (FK `PROTECT`), `tagline` (≤ 160), `description` (Markdown), `rules` (Markdown), `objectives` (Markdown), `cover_image` (optional), `access_mode` ∈ {`open`, `request`, `invite`}, `listed` (bool; for `invite`, whether to show the title in the catalogue), `allow_member_uploads` (bool), `require_post_review` (bool, default false), `status` ∈ {`active`, `suspended`, `archived`}, `created_by`, `tags` (M2M), `search_vector`, `member_count` (denormalized, updated by service).
- `CommunityMembership`: `community`, `user`, `role` ∈ {`member`, `contributor`, `expert`, `moderator`, `animator`, `owner`}; unique (`community`, `user`); `joined_at`; `notification_level` ∈ {`all`, `highlights`, `none`}, **default `highlights`** (published announcements, REX and articles, new documents, community events; replies and personal mentions are always notified whatever the level). Plain discussions and questions appear in the feed, not in the bell, except at level `all`.
- `MembershipRequest`: `community`, `user`, `message` (≤ 500), `status` ∈ {`pending`, `accepted`, `rejected`, `cancelled`}, `decided_by`, `decided_at`, `decision_note`; partial unique index on (`community`, `user`) where `status = pending`.
- `CommunityInvitation`: `community`, `invited_user`, `invited_by`, `role` (≤ `animator`), `status` ∈ {`pending`, `accepted`, `declined`, `revoked`, `expired`}, `expires_at` (14 days).
- `CommunityCreationRequest` (for employees without the `community_creator` role): name, category, justification, status, decider.

### Business rules (services)

- A community **always has at least one `owner`**: the last owner can neither leave nor be demoted (`last_owner` error).
- `join(user, community)`: `open` → member; `request` → creates a request (idempotent if already pending); `invite` → refusal (`invite_only`).
- `accept_request` / `reject_request`: animator+; notification to the requester.
- `invite(user)`: animator+; a role ≥ `animator` can only be granted by an owner.
- `leave`: deletes the membership, keeps the content (author unchanged).
- `change_role`: owner (or functional admin); audited.
- `suspend` / `archive`: functional admin (an owner can archive their own); a suspended or archived community is read-only; archived = out of the catalogue and out of search by default.
- All member modifications update `member_count` in the same transaction (`F()`).

### Visibility (`Community.objects.visible_to(user)`)

- `open`: metadata **and content** readable by any active employee (including in search); publishing, commenting, reacting, uploading a document and registering for an event require membership (one click, offered at the time of the action).
- `request`: metadata (title, description, rules, leads, member count) visible to any active employee; content reserved for members.
- `invite`: visible to members; title only visible to others if `listed`, otherwise 404.
- Functional admin: metadata of all; private content only through the "Access as administrator" (FR: « Accéder en tant qu'administrateur ») action with a mandatory reason, audited.

### Pages

Catalogue (filters category, access mode, "my communities" (FR: « mes communautés »), sort: recent activity / members / alphabetical; search by name); detail (header, contextual join / request / leave / pending button, tabs: Feed, Resources, REX, Events, Members, About); management (settings, members and roles, requests, invitations); creation.

### Acceptance

Visibility matrix tested for the 3 modes × {non-member, member, animator, admin}; last owner protected; two simultaneous requests → a single pending one (constraint); role changes audited.

---

## 7. Work package L4 — Posts and interactions

### Models

- `Tag`: unique `name` (normalized lowercase, accent-free for the key), `slug`. Free creation by contributors+, merge by admin.
- `Post`: `community` (`PROTECT`), `author` (`SET_NULL` + frozen `author_display` for anonymization), `kind` ∈ {`discussion`, `question`, `announcement`, `article`}, `title` (≤ 200), `body` (Markdown ≤ 50,000), `body_html` (sanitized rendering, cached in the database), `status` ∈ {`draft`, `pending_review`, `published`, `hidden`, `archived`}, `pinned_at`, `accepted_answer` (FK Comment, for `question`), `tags`, `last_activity_at`, `comment_count`, `reaction_counts` (denormalized by type), `search_vector`.
- `PostRevision`: `post`, `editor`, `title`, `body`, `created_at` (created on each edit of a published post of type `article`/`announcement`, and for all types if modified by a moderator).
- `Comment`: `post`, `author`, `parent` (nullable FK; depth ≤ 2 checked by service), `body` (≤ 10,000), `status` ∈ {`visible`, `hidden`}, `is_expert_answer` (automatic if the author is expert+).
- `Reaction`: `user`, `post` or `comment` (two nullable FKs + CHECK exactly one), `kind` ∈ {`useful`, `thanks`, `insightful`} (no "dislike"); unique per (user, target, kind).
- `Mention`: `source` (post/comment), `mentioned_user`; resolved on save from `@firstname.lastname` (autocomplete among community members).
- `Bookmark`: `user`, restricted generic target (Post, Document, Rex) through nullable FKs + CHECK; `collection` (nullable FK `BookmarkCollection`); unique.
- `ContentReport`: `reporter`, target (post/comment/document), `reason` ∈ {`inappropriate`, `confidential`, `outdated`, `spam`, `other`}, `details`, `status` ∈ {`open`, `resolved`, `dismissed`}, `handled_by`, `handled_at`, `resolution_note`.

### Rules

- Publish: member+ (`active` community); `announcement`: animator+; `article`: contributor+.
- If `require_post_review`: `pending_review` status until validated by a moderator+.
- Pin: animator+, maximum 3 pinned posts per community.
- Edit: author (on their own content, as long as it is not hidden) or moderator+ (revision created, author notified).
- Hide (`hidden`): moderator+; mandatory reason; audited; the author is notified and still sees their content marked "hidden".
- Question: the author or an expert+ can mark an accepted answer.
- Report: any reader; a user can have only one open report per target; 3 distinct open reports → automatic provisional hiding + moderator alert (configurable threshold).
- Sharing between communities: **quote link** ("also published in" (FR: « publié aussi dans »)) creating a `discussion` post that references the original; the original content remains subject to its own rights (a reader without access sees "content not accessible" (FR: « contenu non accessible »)).
- Rate limits: 10 posts/h, 60 comments/h, 120 reactions/h per user.

### Pages

Community feed (pinned on top, filters by type, "unanswered questions"); personal home feed (followed communities, sorted by activity); post detail (comments, replies, reactions, revisions for moderators); editor with preview; moderation queue (reports, content in review); bookmarks.

---

## 8. Work package L5 — Documents

### Models

- `Document`: `community` (`PROTECT`), `title`, `description`, `doc_type` ∈ {`guide`, `template`, `presentation`, `reference`, `code_sample`, `other`}, `tags`, `owner` (author), `visibility` ∈ {`community`, `restricted`} (restricted: minimum roles `min_role` to read), `download_min_role` (default `member`), `status` ∈ {`active`, `archived`, `expired`}, `expires_at` (optional), `review_due_at` (optional), `current_version` (FK), `download_count`, `search_vector`.
- `DocumentVersion`: `document`, `version_label` (auto `1`, `2`… or label), `storage_key` (unique, random, without the original name), `original_filename` (sanitized), `mime_type` (detected), `size`, `sha256`, `scan_status` ∈ {`pending`, `clean`, `infected`, `error`}, `scanned_at`, `uploaded_by`, `change_note`, `is_reference` (partial unique index per document).
- `DocumentLink`: typed links to `Post`/`Rex`/`Event` (and later trainings).
- `DownloadLog`: `version`, `user`, `created_at` (justification: statistics of the most consulted resources and traceability of restricted documents; retention 12 months).

### Upload flow

1. Form (≤ 100 MB, configurable) → **streaming** upload to the object store under the `quarantine/` prefix.
2. Synchronous validation: extension ∈ whitelist (pdf, docx, xlsx, pptx, odt/ods/odp, txt, md, csv, png, jpg, svg **refused**, zip, ipynb, sql, py…), MIME type detected on the content (`python-magic`) consistent with the extension, size, sanitized name.
3. `DocumentVersion` created with `scan_status=pending`; Celery task `scan_document_version` (exponential retries × 5) → `clamd` in streaming → `clean`: object moved to `documents/`; `infected`: object deleted, author and moderators alerted, audit.
4. While `pending`/`error`, the version is **not downloadable** ("scan in progress" badge (FR: « analyse en cours »)).

### Download

`GET /documents/<public_id>/download/[<version>]` → `policies.can_download` → `DownloadLog` → empty Django response with the **`X-Accel-Redirect: /_protected/<storage_key>`** header; Nginx serves the file from an `internal` location that relays to the private object store (Nginx → storage authentication through service credentials or a signed URL generated server-side and never returned to the client). The application sets `Content-Disposition: attachment; filename*=…`, detected `Content-Type`, `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store`. Preview at the MVP: images and PDF only, same mechanism with `inline` and `Content-Security-Policy: sandbox; default-src 'none'`; other formats: download only. In dev (without Nginx in front of `runserver`), a streaming `FileResponse` fallback is used, enabled only if `DEBUG`.

No storage URL is ever exposed to the browser; the bucket has **no public policy**; every download is re-authorized. Tests: direct access to the bucket denied, `/_protected/` path called directly from outside → 404 (`internal` location), user removed from the community → 404 on the next download.

### Lifecycle

Daily task: `expires_at` exceeded → `expired` (hidden from lists, still accessible to the owner and animators); `review_due_at` exceeded → appears in "content to review" on the lead dashboard; `outdated` report → same.

---

## 9. Work package L6 — REX and articles

### Models

- `RexTemplate`: `name`, `description`, ordered sections (`RexTemplateSection`: `key`, `label`, `help_text`, `required`). Default template provided (data migration) with the sections of § 8 of the prompt: context, problem statement, constraints, approach, solutions considered, decisions and justifications, difficulties, results, lessons learned, good practices, points of attention, tools and technologies.
- `RexArticle`: `community`, `template`, `title`, `summary`, `author`, `contributors` (M2M), `status` ∈ {`draft`, `in_review`, `changes_requested`, `published`, `archived`}, `technologies` (M2M `Tag`), `tags`, `client_sector` (optional closed list, **never the client's name**), `published_at`, `search_vector`, `sensitivity_flags` (result of the last scan, see below).
- `RexSection`: `article`, `key`, `body` (Markdown); unique (`article`, `key`).
- `RexReview`: `article`, `reviewer`, `decision` ∈ {`approve`, `request_changes`}, `comment`, `created_at`.
- `SensitiveTerm` (administrable): `term`, `kind` ∈ {`client_name`, `project_codename`, `other`}, `is_active`.

### Rules

- Auto-saved draft (HTMX, every 15 s if modified, `version` field to avoid concurrent overwrite → 409 + message).
- Submission → synchronous **sensitivity scan** (local rules, no data sent outside): secret patterns (AWS/Azure/GCP keys, GitHub tokens, JWT, connection strings, PEM private keys), e-mails, phone numbers, IBAN, `SensitiveTerm` terms. Result: list of findings displayed to the author with location; secrets → **submission blocked**, unless a waiver is requested (see below); other findings → submission possible but flagged to the reviewer.
- Example values ignored: administrable list of known values (e.g. `AKIAIOSFODNN7EXAMPLE`, providers' documentation keys) and explicit markers in the value (`EXAMPLE`, `changeme`, `xxxx`, `<your-key>`).
- **Block waiver**: the author can request a reasoned waiver (`SensitivityWaiver`: target, finding, reason, requester, decider, decision, date); a moderator+ of the community (other than the author) accepts or refuses it; decision audited; the content stays unpublished until the waiver is accepted.
- The scan runs only on submission/publication and on editing published content; it never silently modifies content and sends no data to an external service.
- The administrator guide reminds that a real secret that was published must be **revoked at the source**, masking in the platform not being sufficient.
- Review: expert+ of the community (not the author nor a contributor); `approve` → `published` + notification to members (level `all`); `request_changes` → back to the author.
- Technical articles, tutorials and guides of the MVP are `Post(kind=article)`; same sensitivity scan applied to any `Post` and `Comment` (secrets → blocking with possible waiver, the rest → warning).

---

## 10. Work package L7 — Events

### Models

- `Event`: `community` (nullable = global event, created by functional admin), `kind` ∈ {`workshop`, `webinar`, `rex_session`, `conference`, `demo`, `hackathon`, `meeting`, `certification_prep`, `training`}, `title`, `description`, `starts_at`, `ends_at` (UTC, CHECK `ends_at > starts_at`), `timezone`, `location` (text), `online_url` (https only, visible to registrants), `capacity` (nullable = unlimited, CHECK ≥ 1 if set), `registration_opens_at`, `registration_closes_at`, `status` ∈ {`draft`, `published`, `cancelled`, `completed`}, `organizer`, `speakers` (M2M User), `resources` (through `DocumentLink`), `registered_count` (denormalized).
- `EventRegistration`: `event`, `user`, `status` ∈ {`registered`, `waitlisted`, `cancelled`}, `waitlist_position` (nullable), `created_at`, `cancelled_at`; unique (`event`, `user`).

### Rules (all under `transaction.atomic` + `select_for_update` on the `Event`)

- The lock on the `Event` row serializes registrations for a given event (estimated capacity well above the peak of 1,000 registrations in 2 min); idempotence through `idempotency_key` against double clicks; dedicated Locust "registration opening" scenario.

- Registration: published event, open window, authorized reader (member if the community is not `open`); free seat → `registered`; otherwise → `waitlisted` with position; re-registration after cancellation reuses the row.
- Unregistration of a `registered` person → promotion of the first `waitlisted` (FIFO) → notification + e-mail.
- Event cancellation (organizer/animator+): mandatory reason, all registrants notified, `cancelled` status.
- Date/place change of a published event → notification to registrants.
- Reminders: Beat task every 15 min, D-1 and H-1 reminders, idempotent (unique `ReminderSent(event, user, kind)`).
- `.ics` export per event (and personal "my registrations" feed through a revocable token URL).
- Participant list: visible to the organizer, animators+ and, as a number only, to others.

### Pages

Global calendar (month/list view, community and type filters), community calendar, detail (registration, capacity, waiting list, resources), management (editing, participants, cancellation), "past events" history.

---

## 11. Work package L8 — Notifications

### Models

- `Notification`: `recipient`, `category` ∈ {`community_post`, `reply`, `mention`, `new_resource`, `invitation`, `membership_decision`, `event_reminder`, `event_change`, `announcement`, `moderation_alert`, `review_request`, `system`}, `actor` (nullable), `verb`, `target_type`, `target_id`, `url`, `read_at`, `created_at`, `emailed_at`; index (`recipient`, `read_at`, `-created_at`).
- `NotificationPreference`: `user`, `category`, `in_app` (bool), `email` ∈ {`immediate`, `daily_digest`, `off`}; unique (`user`, `category`). **Essential** categories that cannot be disabled in-app: `invitation`, `membership_decision`, `event_change`, `moderation_alert` (for moderators), `system`.

### Operation

- `notify(category, recipients, actor, target)` service called **after commit** (`transaction.on_commit`); bulk insertion (`bulk_create`); the author of the action is never notified.
- Mass broadcast (publication of a "highlight" in a community of 2,000 members): done by a Celery task in batches of 500, recipients filtered according to `notification_level`.
- E-mails: Celery task (retries × 5, backoff), `immediate` ones grouped per 5 min window per recipient; daily digest at 8 am (user time zone); per-category unsubscribe link.
- Interface: bell with counter (refreshed by HTMX every 60 s and on every navigation), notifications page (filterable, "mark all read"), preferences page.
- Purge: **all** notifications older than 90 days (read or not) deleted by a nightly task, in batches.

---

## 12. Work package L9 — Search

- Each searchable model (`Community`, `Post`, `Comment` through its post, `Document`, `RexArticle`, `Event`, discoverable `User`, `Tag`) has a `search_vector` (`SearchVectorField`) updated by the write service (weighting: title A, tags B, body C) with the `french` configuration + `unaccent` extension (custom text configuration `fr_unaccent`); GIN index. **Open point for L9 (bilingual UI):** content may be written in English or French; L9 must choose between a per-document language column with the matching configuration (`english` or `fr_unaccent`) and a language-agnostic `simple` + `unaccent` configuration, measured on real content.
- Suggestions: `pg_trgm` on community titles, tags and discoverable user names (GIN trigram index), from 2 characters, 8 results, 250 ms debounce (HTMX).
- **"All" tab** (FR: « Tout »): the top 3 results of each type, in sections, with a "see the N results" (FR: « voir les N résultats ») link to the type's tab; no merged list nor cross pagination (scores not comparable across types). A proven need for unified ranking would trigger the evaluation of a dedicated engine (OpenSearch).
- Per-type tabs: `websearch_to_tsquery` query, counters (capped at "1,000+" to bound the cost), filters (community, type, tag, period), sort (relevance `ts_rank_cd` + freshness, or date), `ts_headline` highlighting on an excerpt; pagination 20.
- **Rights**: each type is queried through `visible_to(user)` then ranked; never post-filtering. `hidden`, `draft`, `pending_review`, archived content and non-`clean` documents are excluded.
- Search history: not kept at the MVP (no proven value, personal data avoided).

---

## 13. Work package L10 — Dashboards and indicators

### Definitions (H11 of the framing document)

- **Active** over a period: `last_seen_at` or a recorded action within the period.
- **Contributor**: created a post, comment, REX, document or published event within the period.
- Community indicators count **current** members.

### Aggregates

`analytics.DailyCommunityStats` table (community, day: members, new members, departures, actives, contributors, posts, comments, downloads, event registrations) and `DailyPlatformStats`, computed by an idempotent nightly task (`upsert` per day); `UserDailyActivity` table (user, day) fed at most once a day per user (for DAU/WAU/MAU), purged after 13 months.

### Dashboards

- **Employee (home)**: my communities, recent feed, upcoming events (my registrations then suggestions), resources recently added in my communities, my recent contributions, unread notifications, explained community suggestions ("shares the tag *Kubernetes* with your interests" (FR: « partage le tag *Kubernetes* avec vos intérêts »)).
- **Lead** (animator+): members and 90-day trend (chart), actives/contributors 28 days, publications, event participation, most downloaded documents, pending requests, unanswered questions > 48 h, reported content, content to review. **No named ranking** of members.
- **Admin**: active users (DAU/WAU/MAU), communities by status, activity trends, document volume (count, total size), pending reports, Celery queues (length, failures 24 h), latest scan errors.

Chart.js charts with a text alternative (accessible data table under each chart).

---

## 14. Work package L11 — MVP hardening and delivery

- `manage.py seed_demo` command (refused if `DJANGO_ENV=production`): 200 fictitious users, 12 categories, 15 communities, content, documents, events.
- Playwright: journeys P1 to P6 of the framing document on 3 screen sizes.
- Locust: reference scenario and `docs/performance.md` report.
- Backup: scheduled `pg_dump` + bucket replication/versioning; script and **tested restore** procedure in a test environment (`docs/runbooks/backup-restore.md`).
- Documentation: README, installation, configuration, development, architecture, database (generated + commented), API (OpenAPI served on `/api/schema/` and `/api/docs/` for authenticated users), user guide, administrator guide, deployment, diagnostics, known limitations.

---

## 15. API (MVP)

DRF REST API with limited read/write, session-authenticated (same origin, CSRF); useful for interactions and future integrations, without duplicating the whole UI:
`/api/v1/communities/`, `/communities/{slug}/members/`, `/posts/`, `/posts/{id}/comments/`, `/documents/`, `/events/`, `/events/{id}/registration/`, `/notifications/`, `/search/`. Pagination, filters, same `policies` and selectors as the UI. DRF rate limiting per user. CORS: no origin allowed by default.

---

## 16. Error handling

| Case | Behavior |
|---|---|
| Form validation | Inline errors, summary at the top of the form with links, focus on the first field in error |
| `DomainError` | Toast + explicit message (e.g. "This community must keep at least one lead" (FR: « Cette communauté doit garder au moins un responsable »)) |
| Edit conflict | 409, offers to reload while keeping the entered text |
| Rate limit | 429 with a dedicated page and retry delay |
| Object store unavailable | Upload refused with a message; download → 503; `/readyz` stays OK (partial degradation), alert |
| Redis unavailable | Cache disabled (fallback), failed tasks visible in the admin dashboard; `cached_db` sessions continue |
| SMTP unavailable | Celery retries; the user is never blocked |
| ClamAV unavailable | Versions stay `pending`; admin alert; nothing is published without a scan |
| Unhandled exception | Static 500, `request_id` displayed for support, event sent to the error collector |

---

## 17. Testing strategy

- **Unit** (services, policies, sensitivity scan, sanitized Markdown rendering).
- **Authorization matrix**: parameterized tests (role × action × object state) for each policy, + HTTP tests verifying 404/403.
- **Integration**: real PostgreSQL, SeaweedFS (S3-compatible) and ClamAV as CI services (EICAR for the antivirus), Celery in `eager` mode except dedicated tests.
- **Concurrency**: simultaneous registrations for the last seat (threads + `TransactionTestCase`), doubled membership requests.
- **Queries**: SQL query ceiling on list and detail pages.
- **Migrations**: from an empty database and `makemigrations --check`.
- **E2E**: Playwright P1–P6, axe-core.
- **Security**: headers (CSP, HSTS), cookies, CSRF, XSS (malicious Markdown), direct access to a bucket object (403), IDOR on `public_id`.
- **Coverage**: ≥ 85% on `services.py` and `policies.py` (blocking), tracked globally.

---

## 18. Implementation order

L0 → L1 → L2 → L3 → L4 → L5 → L6 → L7 → L8 → L9 → L10 → L11. Each package is delivered with tests, migration, documentation and verification instructions, and leaves the application functional. L8 (notifications) exposes `notify()` from L3 in a minimal form (in-app) to avoid revisiting previous packages.

---

## Stress Test Results: MVP specification

### Resolved Decisions
- Private files: served by Nginx through `X-Accel-Redirect` after access control, instead of 60 s presigned URLs; no storage URL exposed (§ 8).
- Open communities: content readable by any active employee, including in search; writing reserved for members; on-request communities: metadata visible, content reserved (§ 6).
- Secret detector: example values ignored, blocking kept with a reasoned waiver validated by a moderator and audited (§ 9).
- Notifications: default level "highlights"; discussions in the feed only; purge of all notifications older than 90 days (§ 6, § 11).
- Search: "All" tab in sections (3 results per type); pagination and sorting per type only (§ 12).
- Accounts: name kept after departure; full anonymization on request or 3 years after deactivation; export of one's data (§ 4).
- SSO: `local` / `mixed` / `sso_only` modes, linking by the IdP's immutable identifier, single emergency account in SSO-only (§ 4).
- Concurrency: exact counters for members and registrants, deferred for reactions/comments/downloads; idempotency key on sensitive forms; peak scenario tested (§ 2, § 10).

### Changes Made
- Sections 2, 4, 6, 8, 9, 10, 11 and 12 modified accordingly; the framing document (`docs/framing.md`) aligned on downloading through Nginx and the visibility of open communities.
- Additions from the self-review: CSRF for HTMX through a header, allowed link schemes in Markdown, external images not loaded.

### Deferred / Parking Lot
- Legal basis for keeping the name after departure and retention periods: to be confirmed with Talan's DPO (D8).
- Target hosting (D1), graphic charter (D4), actual IdP (D2): default values kept, without blocking work package L0.
- Unified multi-type ranking: to be re-evaluated with a dedicated engine if the need is proven.

### Confidence Assessment
- Overall: High for the authorization model, file flows and concurrency; Medium for the relevance of PostgreSQL search (to be measured on real data).
- Areas of concern: adoption (depends on pilot communities), false-positive rate of the secret detector, GDPR validation.
