# Development guide

## Code organization

```
src/
├── config/              # Django configuration: settings, URLs, WSGI, Celery
│   └── settings/        # base.py (shared), dev.py, test.py, prod.py
└── core/                # cross-cutting building blocks: probes, middleware, logs, tasks
```

Each business application (from work package L1) will follow the same layout: `models.py`, `selectors.py` (reads, always filtered by `visible_to(user)`), `services.py` (transactional writes and audit), `policies.py` (authorization decisions), `views.py`, `tasks.py`, `tests/`.

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
| `LOG_LEVEL` | Log level | `INFO` | no |
| `LOG_JSON` | Logs in JSON format | `true` (`false` in dev) | no |

The values in `.env.example` are reserved for local development and must never be reused elsewhere.

## Migrations

Never applied automatically at startup. In development: `docker compose run --rm migrate`. In CI: `make test` applies all migrations on an empty database and `make lint` fails if a migration is missing.

## Tests

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
