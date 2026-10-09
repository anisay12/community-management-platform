# Development guide

## Code organization

```
src/
├── config/              # Django configuration: settings, URLs, WSGI, Celery
│   └── settings/        # base.py (shared), dev.py, test.py, prod.py
├── core/                # cross-cutting: probes, middleware, request context, error views, i18n, admin site
├── accounts/            # users, profiles, roles, authentication (local, TOTP, OIDC), administration, GDPR
├── organizations/       # organization units and employments (manager hierarchy)
├── taxonomy/            # tags (used by profiles)
├── audit/               # append-only audit log and its retention job
└── locale/fr/           # French catalogue (django.po)
```

Each business application follows the same layout: `models.py`, `selectors.py` (reads, always filtered by `visible_to(user)`), `services.py` (transactional writes and audit), `policies.py` (authorization decisions), `views.py`, `tasks.py`, `tests/`.

## Settings per environment

| Module | Usage |
|---|---|
| `config.settings.dev` | Local development (default of `manage.py`), readable logs, `DEBUG` |
| `config.settings.test` | Tests: in-memory cache, in-memory storage, Celery run eagerly |
| `config.settings.prod` | Staging and production (image default): HTTPS, HSTS, secure cookies; refuses to start without `DJANGO_ALLOWED_HOSTS` |

## Environment variables

| Variable | Purpose | Default | Secret |
|---|---|---|---|
| `DJANGO_SETTINGS_MODULE` | Settings module | `config.settings.dev` (`manage.py`), `config.settings.prod` (image) | no |
| `DJANGO_SECRET_KEY` | Django signing key | none (required) | **yes** |
| `DJANGO_ALLOWED_HOSTS` | Accepted host names, comma-separated | empty (rejected in prod) | no |
| `DJANGO_DEBUG` | Debug mode | `false` | no |
| `DATABASE_URL` | PostgreSQL connection | none (required) | **yes** (password) |
| `DB_CONN_MAX_AGE` | Connection lifetime (s) | `60` | no |
| `REDIS_URL` | Cache, locks, Celery broker | none (required) | **yes** if authenticated |
| `S3_BUCKET` | Documents bucket | none (required) | no |
| `S3_ENDPOINT_URL` | S3-compatible endpoint | empty (AWS) | no |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Storage credentials | empty | **yes** |
| `S3_REGION` | Region | empty | no |
| `EMAIL_URL` | SMTP server (format `smtp://user:pass@host:port`) | `consolemail://` | **yes** if credentials |
| `DEFAULT_FROM_EMAIL` | Sender | `communautes@localhost` | no |
| `METRICS_TOKEN` | Access token for `/metrics` (empty = disabled) | empty | **yes** |
| `SITE_URL` | Public base URL used in e-mail links (never derived from the `Host` header) | `http://localhost:8000` | no |
| `NUM_PROXIES` | Number of reverse proxies in front of the app; `0` = `X-Forwarded-For` is not trusted. Drives the client IP used by the audit log and the login rate limit | `0` | no |
| `AUTH_MODE` | `local`, `mixed` or `sso_only` (see [Authentication modes](#authentication-modes-and-single-sign-on)) | `local` | no |
| `BREAK_GLASS_EMAIL` | Emergency local account that may sign in with a password in every mode | empty | no |
| `OIDC_PROVIDER_NAME` | Key of the `ExternalIdentity` rows | `entra` | no |
| `OIDC_RP_CLIENT_ID` / `OIDC_RP_CLIENT_SECRET` | OIDC application credentials; required when `AUTH_MODE` is not `local` | empty | secret: **yes** |
| `OIDC_OP_AUTHORIZATION_ENDPOINT`, `OIDC_OP_TOKEN_ENDPOINT`, `OIDC_OP_USER_ENDPOINT`, `OIDC_OP_JWKS_ENDPOINT` | Identity provider endpoints; required in SSO modes | empty | no |
| `OIDC_OP_ISSUER` | Exact `iss` expected in ID tokens; required in SSO modes | empty | no |
| `OIDC_ALLOWED_TENANT_ID` | Entra tenant (`tid` claim) the ID token must carry; required if an endpoint contains `/common/` or `/organizations/` | empty | no |
| `DJANGO_ADMIN_PATH` | URL prefix of the Django admin (must end with `/`, no leading `/`); use an unguessable value in production | `django-admin/` | no |
| `DJANGO_ADMINS` | Recipients of operational alerts (break-glass sign-in): `Name <a@b.c>,d@e.f` | empty | no |
| `AUDIT_IP_HASH_KEY` | Key of the HMAC applied to client IPs in the audit log | none (required) | **yes** |
| `AUDIT_RETENTION_DAYS` | Retention of audit events | `365` | no |
| `ACCOUNT_ANONYMIZE_AFTER_DAYS` | Deactivated accounts are anonymized after this delay | `1095` | no |
| `DJANGO_READ_DOT_ENV` | Set to `false` to ignore the `.env` file (the settings tests use it so that a developer's `.env` cannot re-supply values) | `true` | no |
| `DEV_ADMIN_PASSWORD` | Password read by `create_dev_admin --password-from-env` (development only) | none | **yes** |
| any name, e.g. `BOOTSTRAP_ADMIN_PASSWORD` | Password read by `create_admin --password-from-env <VAR>` (first administrator); unset it afterwards | none | **yes** |
| `LOG_LEVEL` | Log level | `INFO` | no |
| `LOG_JSON` | Logs in JSON format | `true` (`false` in dev) | no |

The values in `.env.example` are reserved for local development and must never be reused elsewhere.

## First administrator and MFA enrollment

Accounts are normally created by a functional administrator (`/manage/users/`) and activated through an e-mail link valid 72 hours. The very first administrator has to be created from the command line.

**Development** (`DEBUG` on; the command refuses to run otherwise):

```bash
EMAIL=you@example.com make dev-admin          # prompts for the password
# or, non-interactively:
DEV_ADMIN_PASSWORD='...' docker compose exec -e DEV_ADMIN_PASSWORD web \
  python manage.py create_dev_admin --email you@example.com --password-from-env
```

It creates an active superuser with the `functional_admin` role (password checked against the password validators, 12 characters minimum) and records an audit event.

**Staging and production**: use the audited bootstrap command (it does not depend on `DEBUG`); do not use `createsuperuser`, which writes no audit event:

```bash
python manage.py create_admin --email admin@example.com --first-name Ada --last-name Admin
# or, non-interactively (the variable name is yours to choose):
BOOTSTRAP_ADMIN_PASSWORD='...' python manage.py create_admin \
  --email admin@example.com --password-from-env BOOTSTRAP_ADMIN_PASSWORD
```

It creates an active superuser with the `technical_admin` role, checks the password against the password validators and records a `user.admin_bootstrapped` audit event (no actor). It refuses to run when an active superuser already exists; `--force-additional` creates another one anyway, and the audit event records that the option was used. The command reminds you that MFA enrollment is required at the first sign-in. Functional administrators are then created and given their roles from the administration screen (the account page `/manage/users/<public_id>/`, opened from the list; `<public_id>` is the account's public UUID, not its database ID).

In the Django admin, account status, the activation/deactivation/anonymization dates and the `is_staff`/`is_superuser` flags are read-only: status changes go through `/manage/` (audited, and they end the account's sessions), superusers through `create_admin`. Accounts added from the Django admin are created like those of `/manage/users/new/`: pending, `employee` role, audited and invited by e-mail.

**MFA enrollment.** A second factor (TOTP, any authenticator app) is mandatory for staff and superusers, for holders of the `functional_admin`, `technical_admin` and `auditor` roles, and for the break-glass account. At the first sign-in such a user is redirected to `/accounts/mfa/setup/` (QR code, then confirmation of a first code); afterwards each session goes through `/accounts/mfa/verify/` (five failures within 15 minutes lock the form). Until the session is verified, every page redirects to the setup or verification form, and the Django admin answers 404. The Django admin lives at `DJANGO_ADMIN_PATH` and is reachable only by verified staff.

## Authentication modes and single sign-on

`AUTH_MODE` selects how people sign in:

| Mode | Behaviour |
|---|---|
| `local` (default) | E-mail and password only. OIDC is not loaded. |
| `mixed` | E-mail/password and the "Sign in with your Talan account" button. |
| `sso_only` | Single sign-on; the password form is hidden behind an emergency link (`?local=1`) for the break-glass account. Password reset is disabled. |

Local sign-in is protected by django-axes: five failures for an (e-mail, IP) pair lock it for 15 minutes (HTTP 429 page).

**Setting up Microsoft Entra ID** (OpenID Connect, authorization code flow with PKCE, RS256):

1. Register a web application in the Talan tenant with the redirect URI `<SITE_URL>/oidc/callback/`; create a client secret.
2. Use the **tenant-specific** endpoints, not `/common/` or `/organizations/`: `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/authorize`, `.../token` and `.../discovery/v2.0/keys` for the JWKS, and `https://graph.microsoft.com/oidc/userinfo` for the user endpoint.
3. Set `OIDC_OP_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0` (the exact `iss` of ID tokens; startup fails without it) and, if you must keep a multi-tenant endpoint, `OIDC_ALLOWED_TENANT_ID=<tenant-id>` (startup fails when a multi-tenant endpoint is used without it).
4. Configure the application so that tokens carry a **verified e-mail** claim: an account is linked to its identity provider subject on the first sign-in by matching the e-mail, so an unverified or foreign e-mail must never be accepted. The link is immutable afterwards (`ExternalIdentity`), and the account must be `active` at the provider and in the platform.

   **Trust model of the first link.** The first sign-in trusts the provider's `email` claim to choose the account it binds to; the platform cannot verify it. This is safe only if: the endpoints are tenant-specific (never `/common/` or `/organizations/` without `OIDC_ALLOWED_TENANT_ID`); `OIDC_OP_ISSUER` is the exact issuer of that tenant, so tokens from other tenants are refused; and the `email` claim can only hold addresses of the organisation's **verified domains** (in Entra, emit the user principal name or a mail attribute restricted to verified domains, not an editable or guest-supplied address). Once linked, the account is reached only by its subject (`oid`/`sub`), whatever e-mail later arrives. A session opened by single sign-on ends as soon as the account stops being `active`, even if the change was made outside `/manage/`.
5. Set `AUTH_MODE=mixed` first, check that people can sign in, then `sso_only`.

**Break-glass account.** `BREAK_GLASS_EMAIL` designates one local account that can always sign in with a password (and must use MFA), whatever the mode; every such sign-in e-mails the `DJANGO_ADMINS` recipients. Its password is reset by a technical administrator with `manage.py changepassword`.

**Disabling local passwords.** Once `AUTH_MODE=sso_only` is in place, `python manage.py disable_local_passwords [--dry-run]` sets an unusable password on every account except the break-glass one (it refuses to run in other modes), signs the affected users out and writes an audit event.

## Front-end assets and design tokens

Styles are written in Sass under `src/core/static/core/scss/`. All brand values (colours, fonts, radius, spacing, touch target size) live in `tokens.scss`: they become `--tl-*` CSS custom properties, with a dark set under `prefers-color-scheme: dark`, and they also feed the Bootstrap Sass variables in `app.scss`. Changing the brand charter means editing that one file (keep the contrast ratios documented at its top valid).

Bootstrap, Bootstrap Icons and HTMX are npm dev dependencies pinned to exact versions (`package.json`, `package-lock.json`). Rebuild with `make assets` (Node and npm required; `npm ci` runs when `node_modules/` is missing). The output in `src/core/static/core/dist/` (`app.css`, `bootstrap.bundle.min.js`, `htmx.min.js`, `fonts/`, `LICENSES.txt`) is committed on purpose: the Docker image and the Python test environment need no Node toolchain, and the build is deterministic. `make assets-check` (run in CI) rebuilds and fails if the committed output differs, so always commit regenerated files together with the source change.

## Languages and internationalization

The interface is available in English and French. The language is resolved from the saved preference (`UserProfile.language`, empty = automatic), then the `django_language` cookie, then `Accept-Language`, then English; URLs carry no language prefix. The language switcher in the layout and the preferences page (`/me/preferences/`) set it. To try the French interface: open `/me/preferences/` and pick "Français", use the switcher in the header, or send `Accept-Language: fr`.

Every user-facing string is wrapped in `gettext`/`gettext_lazy`/`{% translate %}`, including model `verbose_name`s, choices, forms and e-mails. Workflow:

```bash
make messages     # extracts strings into src/locale/fr/LC_MESSAGES/django.po and compiles
# translate the new msgstr entries in django.po (natural, professional French)
make i18n-check   # CI gate: fails if the catalogue is out of date, or has untranslated or fuzzy entries
```

Compiled `.mo` files are not committed: `make messages`, `make test`, CI and the Docker build compile them.

## Audit log

Every account, role or status change, every sign-in anomaly and every privacy operation writes an `AuditEvent` in the same transaction as the change (`audit.services.record`). Events store the actor, the action code, the target, a non-sensitive change summary, the request ID and an HMAC of the client IP (`AUDIT_IP_HASH_KEY`), never the IP. The model refuses updates and deletions; the Django admin shows them read-only. Events older than `AUDIT_RETENTION_DAYS` are purged by a nightly job.

**Audit log page.** The read-only page lives at `/audit/` (detail at `/audit/<id>/`, GET only, 50 events per page, filters by action, actor, target and dates). Superusers and `auditor` see every event; `technical_admin` sees technical and security events (`auth.*`); `functional_admin` sees functional events (every other action); a user holding several roles sees the union. Everyone else, including anonymous visitors, gets a 404, as does an event outside the viewer's scope. Auditors and administrators need a verified MFA session like on the other administration pages. Nothing can be edited, deleted or exported from the page.

**Production database role.** The application-level guard is not enough against a compromised application: in production the role used by `DATABASE_URL` should hold only `SELECT` and `INSERT` on `audit_auditevent` (no `UPDATE`, no `DELETE`; the `actor` foreign key is `ON DELETE SET NULL` and is not exercised since accounts are anonymized, not deleted). `DELETE` is needed only by the purge job (`audit.tasks.purge_audit_events`): grant it to a separate maintenance role, or grant it for the duration of the job. The platform does not yet run the purge under a second database connection, so this split is an operational decision to take with the hosting team before go-live.

## Scheduled jobs

The `beat` service (Celery beat) runs these tasks (time zone `Europe/Paris`):

| Time | Task | Effect |
|---|---|---|
| 03:15 | `audit.tasks.purge_audit_events` | Deletes audit events older than `AUDIT_RETENTION_DAYS` |
| 03:30 | `accounts.tasks.purge_expired_exports` | Deletes personal-data exports after their 7-day lifetime |
| 03:45 | `accounts.tasks.anonymize_expired_accounts` | Anonymizes accounts deactivated for more than `ACCOUNT_ANONYMIZE_AFTER_DAYS` |

Data exports and e-mails are sent by the `worker` service. Run exactly one `beat` instance.

## Bulk user import (CSV)

Functional administrators import accounts from `/manage/users/import/`. The file is UTF-8 (BOM accepted), at most 2 MB and 5,000 rows, with the exact header (comma or semicolon separated):

```csv
email,first_name,last_name,unit_code,manager_email
jane.doe@example.com,Jane,Doe,ENG,john.smith@example.com
john.smith@example.com,John,Smith,ENG,
```

`unit_code` must be the code of an existing organization unit (may be empty); `manager_email` may reference a line of the same file or an existing account. The import is all-or-nothing: if any line is invalid, nothing is created and each error is reported with its line number. Created accounts are `pending` with the `employee` role and receive an activation e-mail.

## Smoke test of the development environment

```bash
cp .env.example .env && docker compose up -d --build --wait
EMAIL=admin@example.com make dev-admin
```

Sign in at http://localhost:8000/accounts/login/, enrol the TOTP app, create a user in `/manage/users/new/`, open the activation e-mail in Mailpit (http://localhost:8025), activate the account and switch to French in `/me/preferences/`.

## Migrations

Never applied automatically at startup. In development: `docker compose run --rm migrate`. In CI: `make test` applies all migrations on an empty database and `make lint` fails if a migration is missing.

## Tests

- `make i18n-check` : French catalogue up to date and complete.
- `make test` : Django unit and integration tests on a real PostgreSQL, minimum coverage 85%.
- `make test-integration` : checks that the bucket is private (anonymous read denied) against a real S3-compatible storage. Locally, with Compose started:

  ```bash
  S3_INTEGRATION_ENDPOINT=http://localhost:8333 S3_INTEGRATION_BUCKET=talan-documents \
    S3_INTEGRATION_ACCESS_KEY=dev-only-s3 S3_INTEGRATION_SECRET_KEY=dev-only-s3-secret \
    make test-integration
  ```

## Observability

- `GET /healthz` : the process responds (no database access).
- `GET /readyz` : PostgreSQL and Redis respond; 503 otherwise, with only the name of the failing component.
- `GET /metrics` : Prometheus metrics, accessible only with `Authorization: Bearer <METRICS_TOKEN>`; blocked by Nginx on the public side, to be scraped on the internal network.
- Every request carries an `X-Request-ID` identifier (reused if supplied and valid, otherwise generated), returned in the response and present in every JSON log line.

## Development storage

As MinIO is no longer distributed on Docker Hub, the development environment uses **SeaweedFS** as S3-compatible storage (`docker/seaweedfs/entrypoint.sh`). No anonymous identity is declared: any unsigned request is rejected. In production, the managed storage of the chosen host replaces it (decision D1 of the framing document).

## Updating pinned images and actions

Third-party images are pinned by digest and pulled through `mirror.gcr.io` (Google's Docker Hub mirror, with no anonymous rate limit). To update a digest:

```bash
docker pull mirror.gcr.io/library/postgres:16-alpine
docker inspect --format '{{index .RepoDigests 0}}' mirror.gcr.io/library/postgres:16-alpine
```

GitHub Actions are pinned by commit SHA:

```bash
git ls-remote https://github.com/actions/checkout 'refs/tags/v4*'
```

Copy the new value into `compose.yaml`, `Makefile`, `docker/Dockerfile` or `.github/workflows/ci.yml`, then run `make ci`.
