# TALAN Communities Platform

Internal platform for professional communities: communities, content, documents, lessons learned, events, then training and skills.

**Status: work packages L0 (technical foundation) and L1 (accounts, roles, audit) delivered.** The application provides bilingual (English/French) accounts with roles, e-mail/password and Microsoft Entra single sign-on, mandatory TOTP for administrators, user administration with CSV import, profiles, personal data export and anonymization, and an append-only audit log with a read-only audit log page (`/audit/`) for auditors and administrators. Communities and content arrive in the following work packages (see the specification).

## Prerequisites

- Docker ≥ 24 with Docker Compose v2
- [uv](https://docs.astral.sh/uv/) ≥ 0.4 (to run tests outside a container)
- Make

## Quick start

```bash
cp .env.example .env
docker compose up -d --build --wait
curl -fsS localhost:8000/readyz
# {"status": "ok", "checks": {"database": true, "redis": true}}
```

Migrations are never applied automatically by the application: the `migrate` service applies them when the development environment starts, and `docker compose run --rm migrate` replays them.

Behind a corporate proxy that intercepts TLS, build the image by supplying the certificate authority (it is not copied into the image):

```bash
docker build --secret id=extra_ca,src=/path/ca.pem -f docker/Dockerfile --target runtime -t talan-communities:dev .
docker compose up -d --no-build --wait
```

## Useful commands

| Command | Effect |
|---|---|
| `make lint` | Ruff (lint + format) and detection of missing migrations |
| `make test` | Migrations on an empty database, tests and coverage (85% threshold) |
| `make i18n-check` | Fails if the French catalogue is incomplete or out of date |
| `make messages` | Extracts and compiles translations |
| `make dev-admin` | Creates a development administrator (`EMAIL=you@example.com`) |
| `make test-integration` | Private S3 storage test (`S3_INTEGRATION_*` variables required) |
| `make security` | Dependency audit, Bandit, secret scanning (gitleaks) |
| `make image` | Image build, Trivy scan, Nginx configuration validation |
| `make ci` | The whole pipeline, as in continuous integration |

`make test` expects PostgreSQL on `localhost:5432` (database `talan_test`, user `postgres`) and Redis on `localhost:6379`, or the URLs provided by `DATABASE_URL` and `REDIS_URL`.

## Local services

| Service | Address |
|---|---|
| Application | http://localhost:8000 |
| S3 storage (SeaweedFS) | http://localhost:8333 |
| Mailpit (development e-mails) | http://localhost:8025 |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

## Documentation

- [Project framing](docs/framing.md)
- [MVP specification](.internal/specs/2026-10-09-mvp-knowledge-design.md)
- [Architecture decisions (ADR)](docs/decisions/INDEX.md)
- [Development guide](docs/development.md)
