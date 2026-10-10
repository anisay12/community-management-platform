# Agent Guide

The single entry point for coding agents (Claude Code, Codex, Copilot, Cursor…) working on
this repository. Read it top to bottom once; afterwards use it as a map. `CLAUDE.md` and
`AGENT.md` point here.

## 1. What this product is

**Talan Communities** is an internal web platform where Talan employees join communities of
practice, publish posts, react, comment, mention colleagues and (later) share documents,
experience reports, events and search. It is a server-rendered Django application with HTMX
for partial updates, available in English (default) and French.

- Product framing: `docs/framing.md`
- MVP specification (work packages L0–L11, source of truth for behaviour):
  `.internal/specs/2026-10-09-mvp-knowledge-design.md`
- Developer guide (env vars, auth modes, jobs, storage): `docs/development.md`
- Architecture decisions: `docs/decisions/` (`INDEX.md` lists them)

### Delivery status

| Package | Scope | Status |
|---|---|---|
| L0 | Technical foundation (Docker, CI, settings, probes, logging, storage) | Done |
| L1 | Accounts, roles, MFA, SSO, GDPR, audit log | Done |
| L2 | Design system and navigation | Done (redesigned since) |
| L3 | Communities: catalogue, membership, invitations, roles, tabs, admin | Done |
| L4 | Posts: editor, feed, comments, reactions, mentions, bookmarks, moderation | Done |
| L5 | Documents | **Next** |
| L6–L11 | REX/articles, events, notifications, search, dashboards, hardening | Not started |

Rule from the spec: **no fake empty screens**. A navigation entry or page for a feature appears
only once that feature is built (the navigation registry hides entries whose URL does not
resolve).

## 2. Stack

| Layer | Choice |
|---|---|
| Language / runtime | Python 3.13, managed with `uv` (`uv sync`, `uv run …`) |
| Web framework | Django 5.2 (modular monolith, apps under `src/`) |
| Database | PostgreSQL (full-text search via `SearchVector`) |
| Cache, locks, broker | Redis; Celery for background and scheduled jobs |
| Files | S3-compatible storage (SeaweedFS in dev), served privately through nginx `X-Accel` (ADR-0001) |
| Auth | Local passwords (argon2) + TOTP (`django-otp`), OIDC SSO (`mozilla-django-oidc`), lockout (`django-axes`); modes in ADR-0002 |
| Front end | Django templates, HTMX, Bootstrap 5.3 compiled with Dart Sass, Bootstrap Icons, self-hosted Inter |
| Rich text | Restricted Markdown (`markdown-it-py`) sanitised by `nh3` (`core/markdown.py`) |
| Observability | `structlog` JSON logs, `django-prometheus` (`/metrics`), `/healthz`, `/readyz` |
| Tests | pytest + pytest-django, Playwright + axe-core for accessibility |
| Lint / security | ruff, bandit, pip-audit, gitleaks, Trivy |

## 3. Repository map

```
.
├── manage.py                 # defaults to config.settings.dev
├── Makefile                  # every quality gate (see § 6)
├── pyproject.toml / uv.lock  # Python deps, ruff and pytest config
├── package.json              # Sass, Bootstrap, HTMX, icons, Inter, axe-core
├── scripts/build-assets.sh   # compiles SCSS and vendors front-end files into dist/
├── docker/                   # Dockerfile (targets incl. runtime), nginx, seaweedfs
├── compose.yaml              # dev stack: postgres, redis, s3 (SeaweedFS), clamav, mailpit (:8025), migrate, web, worker, beat
├── .github/workflows/ci.yml  # CI = `make ci` step by step
├── docs/                     # development guide, framing, ADRs
├── .internal/                # specs and plans (not shipped); .internal/sdd/ is scratch
├── .beads/                   # beads issue tracker data (see the end of this file)
└── src/
    ├── config/               # settings/{base,dev,test,prod}.py, urls.py, celery.py, wsgi.py
    ├── core/                 # cross-cutting: nav registry, dashboard, errors, markdown,
    │                         # middleware, request context, i18n, health/metrics, design system
    ├── accounts/             # users, profiles, roles, login/MFA/OIDC, user admin, GDPR
    ├── organizations/        # OrganizationUnit, Employment (manager hierarchy)
    ├── taxonomy/             # Tag (shared by profiles, communities, posts) + admin pages
    ├── communities/          # communities, memberships, requests, invitations, tabs, admin
    ├── posts/                # posts, revisions, comments, reactions, mentions, bookmarks, reports
    ├── audit/                # append-only AuditEvent log, viewer, retention job
    ├── notifications/        # Notification rows + notify() service (UI arrives in L8)
    ├── templates/            # project-level templates (500.html)
    └── locale/fr/            # French catalogue django.po (+ compiled .mo, not committed)
```

### Apps at a glance

| App | Key models | Notable modules |
|---|---|---|
| `accounts` | `User` (custom), `UserProfile`, `ExternalIdentity`, `UserSession`, `DataExport` | `roles.py` (global roles), `policies.py`, `backends.py`, `oidc.py`, `middleware.py` (MFA enforcement), `privacy.py` (GDPR export/erase), `views_auth/mfa/profile/manage/privacy.py`, `management/commands/create_dev_admin` |
| `organizations` | `OrganizationUnit`, `Employment` | manager hierarchy used by `accounts.roles.is_manager` |
| `taxonomy` | `Tag` | `services.py`, admin views under `urls_admin.py` |
| `communities` | `CommunityCategory`, `Community`, `CommunityMembership`, `MembershipRequest`, `CommunityInvitation`, `CommunityCreationRequest`, `AdminAccessGrant` | `roles.py` (community roles + `role_at_least`), `tabs.py`, split views/urls: read (`views.py`), member actions (`*_actions`), community managers (`*_manage`), platform admin (`*_admin`) |
| `posts` | `Post`, `PostRevision`, `Comment`, `Reaction`, `Mention`, `BookmarkCollection`, `Bookmark`, `ContentReport` | `services_posts/interactions/moderation.py`, `selectors_moderation.py`, `rendering.py`, `mentions.py`, `hiding.py`, `ratelimit.py`, `privacy.py` |
| `audit` | `AuditEvent` | `services.record(...)` strips sensitive keys; viewer for auditors |
| `notifications` | `Notification` | `services.notify(category, recipients, actor=, target=, community=)` stores rows on commit |
| `core` | — | `navigation.py`, `dashboard.py`, `errors.py` (`DomainError`), `markdown.py`, `context.py`, `views_styleguide.py` (`/styleguide/`) |

### URL map (`src/config/urls.py`)

| Prefix | Owner |
|---|---|
| `/` | `core.views.home` (role-based dashboard) |
| `/` (login, logout, MFA, profile, privacy…) | `accounts.urls` |
| `/manage/` | user administration (`accounts.urls.manage_patterns`) |
| `/audit/` | audit log viewer |
| `/communities/` | catalogue, detail, members, actions, manage, admin |
| `/` (feed, posts, editor, interactions, moderation) | `posts.urls` |
| `settings.DJANGO_ADMIN_PATH` | Django admin (custom admin site, MFA protected) |
| `/i18n/setlang/`, `/styleguide/` | language switch, living design-system page |
| `/healthz`, `/readyz`, `/metrics` | probes and Prometheus |
| `/oidc/` | only when `AUTH_MODE` is not `local` |

Error handlers: `core.views_errors.permission_denied / page_not_found / server_error`.

## 4. Architecture rules (follow them in every change)

### Layering inside an app

```
views.py ──► policies.py   (may the user do it?  pure bool predicates, never raise)
   │    ──► selectors.py  (reads; always scoped with visible_to(user))
   └────► services.py     (writes; transactional, enforce policies, audit, notify)
```

- **Views stay thin.** They parse input with a form, call one service or selector, and render.
  No ORM writes in views.
- **Policies** are named `can_<action>(user, obj) -> bool`. They never raise and never query
  more than needed; per-request caches exist (`accounts.roles.user_roles`,
  `communities.policies` membership cache, `prime_membership_cache` against N+1).
- **Selectors** return querysets/objects filtered for the viewer (`visible_to(user)`) and
  exclude archived rows by default.
- **Services** take the actor first, run in `transaction.atomic`, re-check policies and raise
  `core.errors.DomainError(code, message)` (message is user-safe and translated), call
  `audit.services.record(actor=, action=, target=, changes=, community=)` in the same
  transaction, and `notifications.services.notify(...)` for recipients.
- **404 vs 403:** an object the user may not see answers **404** (never reveal existence);
  a visible object with a forbidden action answers **403**.

### Data conventions

- Internal bigint `id`; a UUID `public_id` is the only identifier in URLs; communities also
  have a `slug`.
- `created_at` / `updated_at` in UTC; display in `UserProfile.timezone` (default Europe/Paris).
- Soft archive with nullable `archived_at`; business content uses `on_delete=PROTECT`.
- Exact counters (`member_count`) are updated with `F()` inside the transaction, under a row
  lock; other counters are deferred Celery jobs.
- Sensitive forms carry an `idempotency_key`; HTMX sends CSRF via `hx-headers` on `<body>`;
  never modify state on GET.
- Markdown only through `core.markdown` (links `https/http/mailto`, external links get
  `rel="noopener noreferrer nofollow"`, no raw HTML, no external images).

### Roles

- Global roles (`accounts.roles.Role`, stored as Django groups): `employee`,
  `community_creator`, `functional_admin`, `technical_admin`, `auditor`.
  Privileged roles and auditors must use MFA (`accounts.policies.requires_mfa`).
- Community roles live in `communities.roles.CommunityRole`; compare with `role_at_least`.
- Functional admins reach a private community only through a time-boxed `AdminAccessGrant`.

### Navigation registry (`core/navigation.py`)

Each app registers its menu entries in `AppConfig.ready()`:

```python
navigation.register(NavItem(
    key="communities", label=_("Communities"), url_name="communities:catalogue",
    icon="people", order=20, section=navigation.WORKSPACE,
    is_visible=authenticated, active_prefixes=("/communities/",),
))
```

Entries are hidden when the URL does not resolve or `is_visible(user)` is false; sections are
`WORKSPACE` then `ADMINISTRATION`. Add a page → register it here; do not hard-code links in
`components/navbar.html`.

### Home dashboard

`core.dashboard.dashboard_context(user)` builds role-specific blocks (member, community
manager, admin, auditor) rendered by `core/home.html`. Add a block there when a new feature
has something to show on the home page, and test it in `core/tests/test_dashboard.py`.

## 5. Front end and design system

- Sources: `src/core/static/core/scss/` — `tokens.scss` (colours: Talan navy `#253371`,
  orange accent; spacing, radii, shadows, light/dark), `_base.scss`, `_layout.scss` (sidebar
  shell), `_components.scss`, `app.scss`. JS: `static/core/js/app.js`,
  `sidebar-state.js` (collapsed sidebar stored in `localStorage` key `tl-sidebar`).
- Compiled output `src/core/static/core/dist/` **is committed**. After any SCSS or vendored
  asset change run `make assets` and commit `dist/`; CI runs `make assets-check`.
- Layouts: `core/templates/base.html` (sidebar app shell, `{% block layout %}`),
  `base_minimal.html` (split-screen auth pages).
- Reusable templates in `src/core/templates/components/`: `navbar`, `user_menu`, `hero`,
  `page_header`, `stat_card`, `card`, `tabs`, `modal`, `toast`, `alert`, `avatar`,
  `messages`, `reaction_bar`, `language_switcher`. Include them instead of copying markup.
- Utility vocabulary (prefix `tl-`): `tl-page-header`, `tl-page-eyebrow`, `tl-page-lead`,
  `tl-page-actions`, `tl-section` (+ `-flush`, `-header`, `-link`), `tl-stat`
  (+ `-icon`, `-label`, `-value`, `-warning`, `-danger`), `tl-toolbar`, `tl-chip`, `tl-meta`,
  `tl-list`, `tl-table`, `tl-kv`, `tl-form`, `tl-form-actions`, `tl-article`,
  `tl-empty-state`, `tl-stagger`. Browse them live at `/styleguide/`.
- Accessibility is a gate: WCAG AA contrast, visible `:focus-visible`, keyboard reachable
  menus, `prefers-reduced-motion` respected. New pages get an axe check in
  `src/core/tests/a11y/` (fixture `open_page(path, role=...)` with roles
  `employee` / `admin` / `auditor`).

## 6. Commands

### Local setup (no Docker)

```bash
uv sync                       # Python deps (incl. dev group)
npm ci --no-audit --no-fund   # front-end toolchain
service postgresql start; service redis-server start   # if not running
export DATABASE_URL=postgres://postgres:postgres@localhost:5432/talan_test
export REDIS_URL=redis://localhost:6379/15
```

### Full stack with Docker

```bash
docker compose up -d --build             # web on http://localhost:8000 (see docs/development.md)
EMAIL=you@example.com make dev-admin     # first superuser; MFA enrolment on first login
```

### Quality gates (CI runs exactly these, in this order)

| Command | What it checks |
|---|---|
| `make lint` | ruff check, ruff format --check, no missing migrations |
| `make i18n-check` | French catalogue up to date, no untranslated or fuzzy entry |
| `make assets-check` | committed `dist/` matches `make assets` |
| `make test` | compile messages, migrate, pytest with coverage ≥ 85 % |
| `make test-a11y` | Playwright + axe-core (`uv run pytest -m a11y`) |
| `make test-integration` | S3 storage integration (needs SeaweedFS) |
| `make security` | pip-audit, bandit, gitleaks |
| `make image` | Docker build, Trivy scan, nginx config test |

Handy variants: `uv run pytest src/communities -q`, `uv run pytest -k mention -x`,
`uv run ruff format .`. Playwright browsers may already exist
(`PLAYWRIGHT_BROWSERS_PATH`); do not re-download them needlessly.

### Internationalisation workflow

Every user-facing string uses `gettext` / `{% translate %}` / `{% blocktranslate %}`.
After adding strings: `make messages`, translate the new `msgstr` in
`src/locale/fr/LC_MESSAGES/django.po` (natural professional French), remove `#, fuzzy`
flags, then `make i18n-check`. When merging branches that both touched the `.po`, resolve
with `msgcat --use-first` on both sides and re-run `make messages`.

## 7. Testing conventions

- Tests sit in `src/<app>/tests/test_*.py`; shared fixtures in `src/conftest.py` and
  `src/core/tests/helpers.py`. Settings: `config.settings.test` (in-memory cache and storage,
  eager Celery).
- Test behaviour through services and views, including the permission matrix
  (anonymous → login redirect, hidden → 404, forbidden → 403, allowed → 200/302) and the
  audit event written. See `communities/tests/test_acceptance_matrix.py` as a model.
- Concurrency-sensitive code (memberships, counters) has `test_concurrency.py`-style tests.
- Never skip, xfail or weaken a test to get green.

## 8. Security rules

- Never log or audit secrets (`audit.services` strips keys containing password/token/secret/otp).
- Sanitise every rich text through `core.markdown`; never mark user input `|safe`.
- Private files go through nginx `X-Accel-Redirect` after a policy check (ADR-0001).
- Rate-limit write endpoints that users can spam (see `posts/ratelimit.py`).
- A bandit finding is fixed, or suppressed with a justified `# nosec <id>` on the exact line.
- Respect GDPR flows: new personal data must be covered by `accounts/privacy.py` export/erase
  (and app-level `privacy.py`, as in `posts`).

## 9. How to add a feature (checklist)

1. Read the work package in the spec and create/claim a bead (`bd create`, `bd update --claim`).
2. Models + migration (`public_id`, timestamps, `archived_at`, `PROTECT`), admin registration.
3. `policies.py` predicates, `selectors.py` with `visible_to`, `services.py` with audit + notify.
4. Forms, thin views, `urls.py` (namespaced), include in `config/urls.py`.
5. Templates extending `base.html`, using `components/` and `tl-` classes; register a `NavItem`;
   add dashboard blocks if relevant.
6. Translations (`make messages`, fill French), tests (unit, views, permission matrix, a11y).
7. Update `docs/development.md` (and an ADR for a hard-to-reverse decision).
8. Run the quality gates in § 6, then hand off per *Session Completion* below.

## 10. Git and pull requests

- Conventional commit subjects (`feat(posts): …`, `fix(ui): …`, `chore(i18n): …`,
  `docs: …`), include the bead id when there is one.
- Branch per work package or fix; PRs target `main` and must pass the CI workflow.
- Commit compiled assets and the `.po` together with the change that needs them.

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:46cd31e7 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   bd dolt push
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->
