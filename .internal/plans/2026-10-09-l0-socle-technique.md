# L0 — Socle technique : plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use beads-superpowers:subagent-driven-development (recommended) or beads-superpowers:executing-plans to implement this plan task-by-task. Each Task becomes a bead (`bd create -t task --parent <epic-id>`). Steps within tasks use checkbox (`- [ ]`) syntax for human readability.
>
> Note d'environnement : `bd` (beads) n'est pas installé dans l'environnement cloud de ce projet. Tant que c'est le cas, le suivi des tâches se fait dans la liste de suivi du fil du projet ; chaque tâche reste un commit autonome.

**Goal:** Livrer un projet Django 5.2 LTS démarrable par `docker compose up`, observable (`/healthz`, `/readyz`, `/metrics`, logs JSON corrélés), avec Celery, stockage S3-compatible privé et une CI qui bloque sur qualité, sécurité et tests.

**Architecture:** Monolithe modulaire Django ; code dans `src/` (`config/` pour la configuration, `core/` pour les briques transverses) ; réglages par environnement lus depuis les variables d'environnement ; Docker multi-étapes non-root ; Compose pour le développement ; Nginx comme reverse proxy de référence ; CI GitHub Actions reproductible localement via `make ci`.

**Tech Stack:** Python 3.13 (compatible ≥ 3.12), Django 5.2 LTS, PostgreSQL 16, Redis 7, Celery 5, django-storages (S3), django-environ, structlog, django-prometheus, Gunicorn, Nginx 1.27, uv, Ruff, pytest/pytest-django, Bandit, pip-audit, gitleaks, Trivy.

## Global Constraints

- Django **5.2 LTS** (`django>=5.2,<5.3`) ; Python ≥ 3.12, image Docker en Python 3.13.
- Aucun secret dans le dépôt, les images Docker ou les journaux ; `.env.example` sans valeur secrète réelle (valeurs `dev-only-…` explicitement non secrètes).
- Aucune migration lancée automatiquement au démarrage de l'application ; les migrations passent par une commande explicite (service `migrate` en dev).
- Toutes les chaînes destinées à l'utilisateur passent par `gettext` ; locale `fr`, fuseau `Europe/Paris`, `USE_TZ = True`.
- Images Docker tierces épinglées par **digest** ; actions GitHub épinglées par **SHA de commit**.
- Le bucket de stockage n'a aucune politique d'accès public.
- Conteneurs applicatifs exécutés par un utilisateur non root (UID 10001).
- Pas de dépendance ajoutée sans usage dans ce lot (YAGNI) : pas d'admin Django, de DRF ni d'authentification avant L1.

---

## Structure de fichiers créée par ce lot

```
.
├── .dockerignore
├── .env.example
├── .github/workflows/ci.yml
├── .gitignore
├── Makefile
├── README.md
├── compose.yaml
├── docker/
│   ├── Dockerfile
│   └── nginx/default.conf
├── docs/development.md
├── manage.py
├── pyproject.toml
├── uv.lock
└── src/
    ├── config/
    │   ├── __init__.py          # expose celery_app
    │   ├── celery.py
    │   ├── urls.py
    │   ├── wsgi.py
    │   ├── settings/{__init__,base,dev,test,prod}.py
    │   └── tests/{__init__,test_settings}.py
    └── core/
        ├── __init__.py
        ├── apps.py
        ├── logging.py           # configuration structlog + dictConfig
        ├── middleware.py        # RequestIDMiddleware
        ├── tasks.py             # tâche ping
        ├── views.py             # healthz, readyz, metrics
        └── tests/{__init__,test_health,test_middleware,test_metrics,test_tasks,test_storage_integration}.py
```

---

### Task 1: Squelette du projet, réglages par environnement et sondes de santé

**Files:**
- Create: `pyproject.toml`, `uv.lock` (généré), `.gitignore`, `manage.py`
- Create: `src/config/__init__.py`, `src/config/urls.py`, `src/config/wsgi.py`
- Create: `src/config/settings/__init__.py`, `base.py`, `dev.py`, `test.py`, `prod.py`
- Create: `src/core/__init__.py`, `src/core/apps.py`, `src/core/views.py`
- Test: `src/core/tests/__init__.py`, `src/core/tests/test_health.py`, `src/config/tests/__init__.py`, `src/config/tests/test_settings.py`

**Interfaces:**
- Consumes: rien.
- Produces:
  - Modules de réglages `config.settings.{dev,test,prod}` ; variables d'environnement obligatoires : `DJANGO_SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `S3_BUCKET` ; `prod` exige en plus `DJANGO_ALLOWED_HOSTS` non vide.
  - `settings.REDIS_URL: str`, `settings.METRICS_TOKEN: str`.
  - Vues `core.views.healthz(request) -> JsonResponse`, `core.views.readyz(request) -> JsonResponse` ; fonctions internes `core.views._check_database() -> bool`, `core.views._check_redis() -> bool`.
  - Routes `/healthz`, `/readyz`.

**Acceptance Criteria:**
- `GET /healthz` répond 200 `{"status": "ok"}` sans toucher la base.
- `GET /readyz` répond 200 quand PostgreSQL et Redis répondent, 503 avec `{"status": "unavailable", "checks": {...}}` sinon ; seuls les noms des composants sont exposés, jamais le message d'erreur.
- `POST` sur ces routes → 405.
- `manage.py check --deploy --fail-level WARNING` passe avec `config.settings.prod`.
- `config.settings.prod` refuse de démarrer si `DJANGO_ALLOWED_HOSTS` est vide.

- [ ] **Step 1: Initialiser le projet et les dépendances**

```bash
cd /home/claude/community-management-platform
uv init --bare --python 3.13 --name talan-communities
uv add 'django>=5.2,<5.3' 'psycopg[binary]>=3.2' 'django-environ>=0.12' 'redis>=5' 'structlog>=24' 'celery[redis]>=5.4' 'django-storages[s3]>=1.14' 'django-prometheus>=2.3' 'gunicorn>=23'
uv add --group dev 'pytest>=8' 'pytest-django>=4.9' 'pytest-cov>=5' 'ruff>=0.6' 'bandit>=1.7' 'pip-audit>=2.7'
```

Puis compléter `pyproject.toml` (garder les listes `dependencies` et `dependency-groups` générées par uv) avec :

```toml
[tool.uv]
package = false

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "DJ", "S", "SIM", "RUF"]

[tool.ruff.lint.per-file-ignores]
"src/config/settings/*" = ["F403", "F405"]
"**/tests/**" = ["S101", "S105", "S106", "S310", "S603"]

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings.test"
pythonpath = ["src"]
addopts = "--strict-markers -ra"
markers = ["integration: nécessite un service externe (stockage S3-compatible)"]

[tool.coverage.run]
source = ["src"]
omit = ["*/migrations/*", "*/tests/*", "src/config/wsgi.py"]
```

`.gitignore` :

```gitignore
__pycache__/
*.py[cod]
.venv/
.env
staticfiles/
.coverage
htmlcov/
.pytest_cache/
.ruff_cache/
```

- [ ] **Step 2: Écrire les tests qui échouent**

`src/core/tests/test_health.py` :

```python
import pytest

from core import views


@pytest.mark.django_db
def test_healthz_returns_ok_without_database(client, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readyz_ok_when_dependencies_answer(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": True, "redis": True}}


@pytest.mark.django_db
def test_readyz_503_when_redis_down(client, monkeypatch):
    monkeypatch.setattr(views, "_check_redis", lambda: False)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": True, "redis": False},
    }


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
def test_probes_reject_post(client, path):
    assert client.post(path).status_code == 405
```

`src/config/tests/test_settings.py` :

```python
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _prod_env(**overrides: str) -> dict[str, str]:
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "DJANGO_ALLOWED_HOSTS": "communautes.example.com",
        "DATABASE_URL": "postgres://u:p@localhost:5432/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "S3_BUCKET": "documents",
    }
    env.update(overrides)
    return env


def _manage(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_prod_settings_pass_deploy_checks():
    result = _manage("check", "--deploy", "--fail-level", "WARNING", env=_prod_env())
    assert result.returncode == 0, result.stdout + result.stderr


def test_prod_settings_require_allowed_hosts():
    result = _manage("check", env=_prod_env(DJANGO_ALLOWED_HOSTS=""))
    assert result.returncode != 0
    assert "DJANGO_ALLOWED_HOSTS" in result.stderr
```

- [ ] **Step 3: Lancer les tests pour vérifier qu'ils échouent**

Préalable local : PostgreSQL et Redis démarrés (`docker compose up -d postgres redis` une fois la Task 4 faite, ou services locaux). Base de test : `createdb talan_test` si nécessaire.

Run: `uv run pytest src/core/tests/test_health.py src/config/tests/test_settings.py -v`
Expected: FAIL / erreur de collecte (`ModuleNotFoundError: No module named 'config'` ou `core`).

- [ ] **Step 4: Implémenter le squelette**

`manage.py` :

```python
#!/usr/bin/env python
import os
import sys
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
```

`src/config/__init__.py` : fichier vide pour l'instant (complété en Task 3).
`src/config/settings/__init__.py` : fichier vide.

`src/config/settings/base.py` :

```python
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = BASE_DIR / "src"

env = environ.Env()
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_prometheus",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [SRC_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

REDIS_URL = env("REDIS_URL")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "tc",
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

LANGUAGE_CODE = "fr"
LANGUAGES = [("fr", "Français")]
LOCALE_PATHS = [SRC_DIR / "locale"]
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("S3_BUCKET"),
            "endpoint_url": env("S3_ENDPOINT_URL", default=None),
            "access_key": env("S3_ACCESS_KEY", default=None),
            "secret_key": env("S3_SECRET_KEY", default=None),
            "region_name": env("S3_REGION", default=None),
            "default_acl": None,
            "querystring_auth": True,
            "file_overwrite": False,
        },
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

vars().update(env.email_url("EMAIL_URL", default="consolemail://"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="communautes@localhost")

METRICS_TOKEN = env("METRICS_TOKEN", default="")
```

`src/config/settings/dev.py` :

```python
from .base import *

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]
```

`src/config/settings/test.py` :

```python
import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-not-a-secret")
os.environ.setdefault("DATABASE_URL", "postgres://postgres:postgres@localhost:5432/talan_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("S3_BUCKET", "test-bucket")

from .base import *  # noqa: E402

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
STORAGES["default"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
METRICS_TOKEN = "test-metrics-token"
```

`src/config/settings/prod.py` :

```python
from django.core.exceptions import ImproperlyConfigured

from .base import *

DEBUG = False
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS doit lister au moins un nom d'hôte.")

CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"
```

`SECURE_HSTS_PRELOAD = False` déclenche l'avertissement `security.W021` ; le préchargement HSTS est une décision d'entreprise (domaine partagé). Ajouter dans `prod.py` :

```python
SILENCED_SYSTEM_CHECKS = ["security.W021"]  # préchargement HSTS : décision de la DSI Talan
```

`src/config/urls.py` :

```python
from django.urls import path

from core import views

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("readyz", views.readyz, name="readyz"),
]
```

`src/config/wsgi.py` :

```python
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_wsgi_application()
```

`src/core/__init__.py` : vide. `src/core/apps.py` :

```python
from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    default_auto_field = "django.db.models.BigAutoField"
```

`src/core/views.py` :

```python
import redis
import structlog
from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = structlog.get_logger(__name__)


@never_cache
@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def _check_database() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except DatabaseError:
        logger.warning("readiness_check_failed", component="database", exc_info=True)
        return False


def _check_redis() -> bool:
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1
        )
        return bool(client.ping())
    except redis.RedisError:
        logger.warning("readiness_check_failed", component="redis", exc_info=True)
        return False


@never_cache
@require_GET
def readyz(request: HttpRequest) -> JsonResponse:
    checks = {"database": _check_database(), "redis": _check_redis()}
    ok = all(checks.values())
    return JsonResponse(
        {"status": "ok" if ok else "unavailable", "checks": checks},
        status=200 if ok else 503,
    )
```

`src/core/tests/__init__.py` et `src/config/tests/__init__.py` : vides.

- [ ] **Step 5: Lancer les tests pour vérifier qu'ils passent**

Run: `uv run pytest src/core/tests/test_health.py src/config/tests/test_settings.py -v`
Expected: 7 passed.

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: aucune erreur (lancer `uv run ruff format .` puis relancer si besoin).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .gitignore manage.py src
git commit -m "feat(socle): projet Django 5.2, réglages par environnement et sondes de santé"
```

---

### Task 2: Journaux JSON corrélés par identifiant de requête

**Files:**
- Create: `src/core/logging.py`, `src/core/middleware.py`
- Modify: `src/config/settings/base.py` (ajout `LOGGING`, middleware), `src/config/settings/dev.py` (rendu console)
- Test: `src/core/tests/test_middleware.py`

**Interfaces:**
- Consumes: `config.settings.base` (Task 1).
- Produces:
  - `core.middleware.RequestIDMiddleware` ; attribut `request.request_id: str` ; en-tête de réponse `X-Request-ID` ; variable de contexte structlog `request_id`.
  - `core.logging.build_logging(level: str = "INFO", json: bool = True) -> dict` et `core.logging.configure_structlog() -> None`.

**Acceptance Criteria:**
- Un `X-Request-ID` entrant valide (`^[A-Za-z0-9-]{8,64}$`) est réutilisé et renvoyé ; sinon un identifiant de 32 caractères hexadécimaux est généré.
- Pendant le traitement de la requête, `structlog.contextvars` contient `request_id`.
- En `prod` et `test`, chaque ligne de log est un objet JSON contenant `timestamp`, `level`, `event`, `logger` et `request_id` quand il existe.

- [ ] **Step 1: Écrire les tests qui échouent**

`src/core/tests/test_middleware.py` :

```python
import json
import logging.config
import re

import structlog
from django.http import HttpResponse
from django.test import RequestFactory

from core.logging import build_logging
from core.middleware import RequestIDMiddleware


def _run(headers: dict[str, str]) -> tuple[HttpResponse, dict]:
    seen: dict = {}

    def view(request):
        seen.update(structlog.contextvars.get_contextvars())
        seen["attr"] = request.request_id
        return HttpResponse("ok")

    request = RequestFactory().get("/", headers=headers)
    response = RequestIDMiddleware(view)(request)
    return response, seen


def test_valid_incoming_request_id_is_propagated():
    response, seen = _run({"X-Request-ID": "abc12345-def"})
    assert response["X-Request-ID"] == "abc12345-def"
    assert seen["request_id"] == "abc12345-def"
    assert seen["attr"] == "abc12345-def"


def test_invalid_incoming_request_id_is_replaced():
    response, seen = _run({"X-Request-ID": "bad id\n<script>"})
    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])
    assert seen["request_id"] == response["X-Request-ID"]


def test_missing_request_id_is_generated():
    response, _ = _run({})
    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])


def test_json_log_line_contains_request_id(capsys):
    logging.config.dictConfig(build_logging(level="INFO", json=True))
    structlog.contextvars.bind_contextvars(request_id="req-12345678")
    try:
        structlog.get_logger("core.test").info("hello", answer=42)
    finally:
        structlog.contextvars.clear_contextvars()
    line = capsys.readouterr().err.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello"
    assert payload["answer"] == 42
    assert payload["request_id"] == "req-12345678"
    assert payload["level"] == "info"
    assert payload["logger"] == "core.test"
    assert "timestamp" in payload
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `uv run pytest src/core/tests/test_middleware.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'core.logging'`.

- [ ] **Step 3: Implémenter**

`src/core/logging.py` :

```python
import structlog

_SHARED_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def configure_structlog() -> None:
    structlog.configure(
        processors=[*_SHARED_PROCESSORS, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def build_logging(level: str = "INFO", json: bool = True) -> dict:
    renderer = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.format_exc_info,
                    renderer,
                ],
                "foreign_pre_chain": _SHARED_PROCESSORS,
            }
        },
        "handlers": {
            "stderr": {"class": "logging.StreamHandler", "formatter": "structured"}
        },
        "root": {"handlers": ["stderr"], "level": level},
        "loggers": {
            "django.request": {"level": "WARNING"},
            "django.db.backends": {"level": "WARNING"},
        },
    }
```

`src/core/middleware.py` :

```python
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
```

Dans `src/config/settings/base.py`, ajouter en tête des imports `from core.logging import build_logging, configure_structlog`, insérer `"core.middleware.RequestIDMiddleware"` **en première position** de `MIDDLEWARE`, et ajouter à la fin :

```python
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOG_JSON = env.bool("LOG_JSON", default=True)
LOGGING = build_logging(level=LOG_LEVEL, json=LOG_JSON)
configure_structlog()
```

Dans `src/config/settings/dev.py`, ajouter :

```python
LOG_JSON = env.bool("LOG_JSON", default=False)
LOGGING = build_logging(level=LOG_LEVEL, json=LOG_JSON)
```

et importer `build_logging` (`from core.logging import build_logging`).

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `uv run pytest -v`
Expected: tous les tests passent (11 passed).

- [ ] **Step 5: Commit**

```bash
git add src
git commit -m "feat(socle): journaux JSON structurés corrélés par X-Request-ID"
```

---

### Task 3: Métriques Prometheus protégées et Celery

**Files:**
- Create: `src/config/celery.py`, `src/core/tasks.py`
- Modify: `src/config/__init__.py`, `src/config/urls.py`, `src/core/views.py`, `src/config/settings/base.py`, `src/config/settings/test.py`
- Test: `src/core/tests/test_metrics.py`, `src/core/tests/test_tasks.py`

**Interfaces:**
- Consumes: `settings.METRICS_TOKEN`, `settings.REDIS_URL` (Task 1).
- Produces:
  - Vue `core.views.metrics(request) -> HttpResponse` sur `/metrics`, protégée par `Authorization: Bearer <METRICS_TOKEN>` ; 404 si jeton absent, faux, ou si `METRICS_TOKEN` est vide.
  - Application Celery `config.celery.app` (exposée en `config.celery_app`), configurée depuis les réglages préfixés `CELERY_`.
  - Tâche `core.tasks.ping() -> str` (renvoie `"pong"`), utilisée par les vérifications d'exploitation.

**Acceptance Criteria:**
- `/metrics` sans jeton ou avec un mauvais jeton → 404 ; avec le bon jeton → 200 et contenu au format Prometheus contenant `django_http_requests`.
- La comparaison du jeton est en temps constant.
- `core.tasks.ping.delay().get()` renvoie `"pong"` en mode test (exécution immédiate).
- Réglages Celery : `acks_late`, `reject_on_worker_lost`, `prefetch_multiplier=1`, limites de temps 240 s / 300 s.

- [ ] **Step 1: Écrire les tests qui échouent**

`src/core/tests/test_metrics.py` :

```python
def test_metrics_hidden_without_token(client):
    assert client.get("/metrics").status_code == 404


def test_metrics_hidden_with_wrong_token(client):
    response = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 404


def test_metrics_exposed_with_token(client):
    client.get("/healthz")
    response = client.get("/metrics", headers={"Authorization": "Bearer test-metrics-token"})
    assert response.status_code == 200
    assert b"django_http_requests" in response.content


def test_metrics_hidden_when_token_not_configured(client, settings):
    settings.METRICS_TOKEN = ""
    response = client.get("/metrics", headers={"Authorization": "Bearer "})
    assert response.status_code == 404
```

`src/core/tests/test_tasks.py` :

```python
from django.conf import settings

from core.tasks import ping


def test_ping_task_runs():
    assert ping.delay().get(timeout=5) == "pong"


def test_celery_reliability_settings():
    assert settings.CELERY_TASK_ACKS_LATE is True
    assert settings.CELERY_TASK_REJECT_ON_WORKER_LOST is True
    assert settings.CELERY_WORKER_PREFETCH_MULTIPLIER == 1
    assert settings.CELERY_TASK_SOFT_TIME_LIMIT == 240
    assert settings.CELERY_TASK_TIME_LIMIT == 300
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `uv run pytest src/core/tests/test_metrics.py src/core/tests/test_tasks.py -v`
Expected: FAIL (`/metrics` → 404 partout, donc `test_metrics_exposed_with_token` échoue ; `ModuleNotFoundError: core.tasks`).

- [ ] **Step 3: Implémenter**

Dans `src/config/settings/base.py` : encadrer `MIDDLEWARE` par les middlewares Prometheus (premier et dernier) :

```python
MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "core.middleware.RequestIDMiddleware",
    # … middlewares existants inchangés …
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]
```

et ajouter à la fin :

```python
CELERY_BROKER_URL = REDIS_URL
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_TASK_TIME_LIMIT = 300
CELERY_TIMEZONE = TIME_ZONE
```

Dans `src/config/settings/test.py`, ajouter :

```python
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
```

`src/config/celery.py` :

```python
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("talan_communities")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
```

`src/config/__init__.py` :

```python
from .celery import app as celery_app

__all__ = ("celery_app",)
```

`src/core/tasks.py` :

```python
from celery import shared_task


@shared_task
def ping() -> str:
    return "pong"
```

Ajouter à `src/core/views.py` :

```python
import hmac

from django.http import Http404, HttpResponse
from django_prometheus.exports import ExportToDjangoView


@never_cache
@require_GET
def metrics(request: HttpRequest) -> HttpResponse:
    token = settings.METRICS_TOKEN
    supplied = request.headers.get("Authorization", "")
    if not token or not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
        raise Http404
    return ExportToDjangoView(request)
```

(fusionner ces imports avec ceux déjà présents en tête de fichier). Dans `src/config/urls.py`, ajouter `path("metrics", views.metrics, name="metrics"),`.

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `uv run pytest -v && uv run ruff check .`
Expected: 17 passed, 1 skipped (test d'intégration stockage, exécuté en Task 4), Ruff sans erreur.

- [ ] **Step 5: Commit**

```bash
git add src
git commit -m "feat(socle): métriques Prometheus protégées par jeton et application Celery"
```

---

### Task 4: Conteneurisation, environnement de développement et stockage privé

**Files:**
- Create: `docker/Dockerfile`, `.dockerignore`, `compose.yaml`, `.env.example`, `docker/nginx/default.conf`
- Test: `src/core/tests/test_storage_integration.py`

**Interfaces:**
- Consumes: `config.wsgi.application`, `config.celery_app`, routes `/healthz`, `/readyz` (Tasks 1–3).
- Produces:
  - Image `talan-communities` (cible `runtime`), utilisateur `app` (UID 10001), `PYTHONPATH=/app/src`, commande par défaut Gunicorn sur `:8000`.
  - Services Compose : `postgres`, `redis`, `minio`, `minio-init`, `clamav`, `mailpit`, `migrate`, `web`, `worker`, `beat`.
  - Variables d'environnement d'intégration stockage : `S3_INTEGRATION_ENDPOINT`, `S3_INTEGRATION_BUCKET`, `S3_INTEGRATION_ACCESS_KEY`, `S3_INTEGRATION_SECRET_KEY`.

**Acceptance Criteria:**
- `cp .env.example .env && docker compose up -d --build --wait` démarre tous les services ; `curl -fsS localhost:8000/readyz` → 200.
- `docker compose exec worker celery -A config inspect ping` répond.
- L'image tourne en non-root (`docker run --rm talan-communities:dev id -u` → `10001`) et ne contient aucun fichier `.env`.
- Le bucket de développement refuse la lecture anonyme (test d'intégration vert contre MinIO).
- `nginx -t` valide `docker/nginx/default.conf`.
- Aucune migration n'est appliquée par `web` ; seul le service `migrate` les applique.

- [ ] **Step 1: Écrire le test d'intégration du stockage (échoue sans service)**

`src/core/tests/test_storage_integration.py` :

```python
import os
import urllib.error
import urllib.request
import uuid

import pytest
from django.core.files.base import ContentFile
from storages.backends.s3 import S3Storage

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("S3_INTEGRATION_ENDPOINT"),
        reason="nécessite un stockage S3-compatible (exécuté en CI et via `make test-integration`)",
    ),
]


def test_private_bucket_roundtrip_and_no_anonymous_read():
    endpoint = os.environ["S3_INTEGRATION_ENDPOINT"]
    bucket = os.environ["S3_INTEGRATION_BUCKET"]
    storage = S3Storage(
        bucket_name=bucket,
        endpoint_url=endpoint,
        access_key=os.environ["S3_INTEGRATION_ACCESS_KEY"],
        secret_key=os.environ["S3_INTEGRATION_SECRET_KEY"],
        default_acl=None,
        querystring_auth=True,
        file_overwrite=False,
    )
    name = storage.save(f"l0-check/{uuid.uuid4().hex}.txt", ContentFile(b"bonjour"))
    try:
        with storage.open(name) as fh:
            assert fh.read() == b"bonjour"
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{endpoint}/{bucket}/{name}", timeout=5)
        assert exc.value.code == 403
    finally:
        storage.delete(name)
```

- [ ] **Step 2: Épingler les images tierces par digest**

```bash
for image in postgres:16-alpine redis:7-alpine minio/minio:latest clamav/clamav:stable axllent/mailpit:latest nginx:1.27-alpine python:3.13-slim ghcr.io/astral-sh/uv:0.11; do
  docker pull -q "$image" >/dev/null && docker inspect --format '{{index .RepoDigests 0}}' "$image"
done
```

Expected: une ligne `nom@sha256:…` par image. Reporter chaque valeur à la place de `<DIGEST:image>` dans les fichiers ci-dessous. Risque connu : la distribution des images communautaires MinIO a évolué en 2025 ; si `minio/minio` n'est plus publié ou n'est plus maintenu, remplacer par une alternative S3-compatible (SeaweedFS ou Garage) dans `compose.yaml` et dans la CI, et le signaler dans le fil du projet avant de poursuivre.

- [ ] **Step 3: Écrire le Dockerfile et `.dockerignore`**

`docker/Dockerfile` :

```dockerfile
# syntax=docker/dockerfile:1.7
FROM <DIGEST:ghcr.io/astral-sh/uv:0.11> AS uv

FROM <DIGEST:python:3.13-slim> AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY manage.py ./
COPY src ./src
RUN DJANGO_SETTINGS_MODULE=config.settings.prod \
    DJANGO_SECRET_KEY=collectstatic-build-only \
    DJANGO_ALLOWED_HOSTS=localhost \
    DATABASE_URL=postgres://build:build@localhost:5432/build \
    REDIS_URL=redis://localhost:6379/0 \
    S3_BUCKET=build \
    /app/.venv/bin/python manage.py collectstatic --noinput

FROM <DIGEST:python:3.13-slim> AS runtime
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build --chown=app:app /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-", "--forwarded-allow-ips", "*"]
```

Les variables passées à `RUN collectstatic` sont des valeurs factices propres à cette commande : elles ne sont pas conservées dans l'image (pas d'`ENV`).

`.dockerignore` :

```
.git
.venv
.env
**/__pycache__
.pytest_cache
.ruff_cache
staticfiles
.internal
docs
```

- [ ] **Step 4: Écrire `.env.example` et `compose.yaml`**

`.env.example` :

```dotenv
# Valeurs de DÉVELOPPEMENT uniquement. Ne jamais réutiliser en recette ou en production.
DJANGO_SETTINGS_MODULE=config.settings.dev
DJANGO_SECRET_KEY=dev-only-change-me-not-a-secret
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
POSTGRES_DB=talan
POSTGRES_USER=talan
POSTGRES_PASSWORD=dev-only-password
DATABASE_URL=postgres://talan:dev-only-password@postgres:5432/talan
REDIS_URL=redis://redis:6379/0
MINIO_ROOT_USER=dev-only-minio
MINIO_ROOT_PASSWORD=dev-only-minio-password
S3_ENDPOINT_URL=http://minio:9000
S3_BUCKET=talan-documents
S3_ACCESS_KEY=dev-only-minio
S3_SECRET_KEY=dev-only-minio-password
EMAIL_URL=smtp://mailpit:1025
DEFAULT_FROM_EMAIL=communautes@localhost
METRICS_TOKEN=
LOG_LEVEL=INFO
```

`compose.yaml` :

```yaml
name: talan-communities

x-app: &app
  build:
    context: .
    dockerfile: docker/Dockerfile
    target: runtime
  image: talan-communities:dev
  env_file: .env
  volumes:
    - ./src:/app/src:ro
  depends_on:
    postgres: { condition: service_healthy }
    redis: { condition: service_healthy }
    minio-init: { condition: service_completed_successfully }

services:
  postgres:
    image: <DIGEST:postgres:16-alpine>
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes: [pgdata:/var/lib/postgresql/data]
    ports: ["127.0.0.1:5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      retries: 10

  redis:
    image: <DIGEST:redis:7-alpine>
    ports: ["127.0.0.1:6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 10

  minio:
    image: <DIGEST:minio/minio:latest>
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes: [miniodata:/data]
    ports: ["127.0.0.1:9000:9000", "127.0.0.1:9001:9001"]
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      retries: 10

  minio-init:
    image: <DIGEST:minio/minio:latest>
    depends_on:
      minio: { condition: service_healthy }
    env_file: .env
    entrypoint: >
      sh -c "mc alias set local http://minio:9000 $$MINIO_ROOT_USER $$MINIO_ROOT_PASSWORD &&
             mc mb --ignore-existing local/$$S3_BUCKET &&
             mc anonymous set none local/$$S3_BUCKET"

  clamav:
    image: <DIGEST:clamav/clamav:stable>
    volumes: [clamdb:/var/lib/clamav]

  mailpit:
    image: <DIGEST:axllent/mailpit:latest>
    ports: ["127.0.0.1:8025:8025"]

  migrate:
    <<: *app
    command: ["python", "manage.py", "migrate", "--noinput"]
    restart: "no"

  web:
    <<: *app
    command: ["python", "manage.py", "runserver", "0.0.0.0:8000"]
    ports: ["127.0.0.1:8000:8000"]
    depends_on:
      migrate: { condition: service_completed_successfully }
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)"]
      interval: 10s
      retries: 10

  worker:
    <<: *app
    command: ["celery", "-A", "config", "worker", "-l", "info"]

  beat:
    <<: *app
    command: ["celery", "-A", "config", "beat", "-l", "info", "--schedule", "/tmp/celerybeat-schedule"]

volumes:
  pgdata:
  miniodata:
  clamdb:
```

Remarque : `web` redéfinit `depends_on` ; ajouter aussi `postgres`, `redis` et `minio-init` dans ce bloc, car la redéfinition remplace celle de l'ancre :

```yaml
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio-init: { condition: service_completed_successfully }
      migrate: { condition: service_completed_successfully }
```

- [ ] **Step 5: Écrire la configuration Nginx de référence**

`docker/nginx/default.conf` :

```nginx
map $http_x_request_id $req_id {
    default   $http_x_request_id;
    ""        $request_id;
}

upstream django {
    server web:8000;
    keepalive 32;
}

server {
    listen 8080;
    server_name _;
    client_max_body_size 100m;
    server_tokens off;

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1024;

    location /static/ {
        alias /app/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location = /metrics {
        return 404;
    }

    location / {
        proxy_pass http://django;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $req_id;
        proxy_read_timeout 60s;
    }

    # L'emplacement interne /_protected/ (ADR-0001) est ajouté au lot L5.
}
```

`/metrics` est bloqué au niveau du proxy public : la collecte Prometheus se fait sur le réseau interne directement vers `web:8000`.

- [ ] **Step 6: Vérifier l'environnement de bout en bout**

```bash
cp .env.example .env
docker compose up -d --build --wait
curl -fsS localhost:8000/healthz
curl -fsS localhost:8000/readyz
docker compose exec worker celery -A config inspect ping
docker compose run --rm --no-deps web id -u
docker run --rm talan-communities:dev sh -c 'test ! -e /app/.env && echo no-dotenv'
docker run --rm --add-host web:127.0.0.1 -v "$PWD/docker/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro" <DIGEST:nginx:1.27-alpine> nginx -t
S3_INTEGRATION_ENDPOINT=http://localhost:9000 S3_INTEGRATION_BUCKET=talan-documents \
  S3_INTEGRATION_ACCESS_KEY=dev-only-minio S3_INTEGRATION_SECRET_KEY=dev-only-minio-password \
  uv run pytest -m integration -v
```

Expected : `{"status": "ok"}` ; `{"status": "ok", "checks": {"database": true, "redis": true}}` ; `pong` du worker ; `10001` ; `no-dotenv` ; `syntax is ok` / `test is successful` ; `1 passed`.

- [ ] **Step 7: Commit**

```bash
git add docker .dockerignore compose.yaml .env.example src/core/tests/test_storage_integration.py
git commit -m "feat(socle): image Docker non-root, Compose de développement et stockage privé"
```

---

### Task 5: Intégration continue et commande `make ci`

**Files:**
- Create: `Makefile`, `.github/workflows/ci.yml`
- Test: le workflow lui-même (exécution verte sur la PR) et `make ci` en local.

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: cibles `make lint`, `make test`, `make test-integration`, `make security`, `make image`, `make ci` ; workflow `ci` déclenché sur `push` de toute branche et sur `pull_request`.

**Acceptance Criteria:**
- La CI échoue si : Ruff (lint ou format) signale un écart, une migration manque (`makemigrations --check`), les migrations ne s'appliquent pas sur une base vide, un test échoue, la couverture passe sous 85 %, `pip-audit` trouve une vulnérabilité, Bandit trouve un problème de sévérité moyenne ou plus, gitleaks trouve un secret, Trivy trouve une vulnérabilité CRITICAL corrigeable, ou `nginx -t` échoue.
- Le test d'intégration stockage s'exécute réellement en CI (MinIO lancé dans le job), il n'est pas ignoré.
- Toutes les actions GitHub sont épinglées par SHA de commit ; toutes les images par digest.
- `make ci` reproduit localement les mêmes contrôles.

- [ ] **Step 1: Écrire le Makefile**

```makefile
.PHONY: lint test test-integration security image ci

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run python manage.py makemigrations --check --dry-run --settings=config.settings.test

test:
	uv run python manage.py migrate --noinput --settings=config.settings.test
	uv run pytest --cov --cov-report=term-missing --cov-fail-under=85

test-integration:
	uv run pytest -m integration -v

security:
	uv export --frozen --no-hashes --no-dev --format requirements-txt > /tmp/requirements-audit.txt
	uvx pip-audit --strict -r /tmp/requirements-audit.txt
	uv run bandit -q -r src -x '*/tests/*' -ll
	docker run --rm -v "$(PWD):/repo" $(GITLEAKS_IMAGE) detect --source /repo --redact --no-banner

image:
	docker build -f docker/Dockerfile --target runtime -t talan-communities:ci .
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock $(TRIVY_IMAGE) image --exit-code 1 --severity CRITICAL --ignore-unfixed talan-communities:ci
	docker run --rm --add-host web:127.0.0.1 -v "$(PWD)/docker/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro" $(NGINX_IMAGE) nginx -t

ci: lint test security image
```

En tête du Makefile, définir les images épinglées obtenues comme en Task 4 Step 2 (ajouter `zricethezav/gitleaks:latest` et `aquasec/trivy:latest` à la boucle de `docker pull`) :

```makefile
GITLEAKS_IMAGE ?= <DIGEST:zricethezav/gitleaks:latest>
TRIVY_IMAGE ?= <DIGEST:aquasec/trivy:latest>
NGINX_IMAGE ?= <DIGEST:nginx:1.27-alpine>
```

Note : la commande `migrate` de la cible `test` vérifie l'application des migrations depuis une base vide ; pytest-django crée ensuite sa propre base de test.

- [ ] **Step 2: Écrire le workflow**

`.github/workflows/ci.yml` :

```yaml
name: ci

on:
  push:
    branches: ["**"]
  pull_request:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  checks:
    runs-on: ubuntu-24.04
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: talan_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres" --health-interval 5s --health-retries 10
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
        options: >-
          --health-cmd "redis-cli ping" --health-interval 5s --health-retries 10
    env:
      DATABASE_URL: postgres://postgres:postgres@localhost:5432/talan_test
      REDIS_URL: redis://localhost:6379/15
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
      - run: uv sync --frozen
      - name: Démarrer MinIO pour le test d'intégration stockage
        run: |
          docker run -d --name minio -p 9000:9000 \
            -e MINIO_ROOT_USER=ci-minio -e MINIO_ROOT_PASSWORD=ci-minio-password \
            ${{ vars.MINIO_IMAGE || 'minio/minio:latest' }} server /data
          for i in $(seq 1 30); do curl -fs http://localhost:9000/minio/health/ready && break; sleep 1; done
          docker exec minio sh -c 'mc alias set local http://localhost:9000 ci-minio ci-minio-password && mc mb local/ci-bucket && mc anonymous set none local/ci-bucket'
      - run: make lint
      - run: make test
      - name: Test d'intégration stockage
        env:
          S3_INTEGRATION_ENDPOINT: http://localhost:9000
          S3_INTEGRATION_BUCKET: ci-bucket
          S3_INTEGRATION_ACCESS_KEY: ci-minio
          S3_INTEGRATION_SECRET_KEY: ci-minio-password
        run: make test-integration
      - run: make security
      - run: make image
```

- [ ] **Step 3: Épingler les actions par SHA**

```bash
gh api repos/actions/checkout/commits/v4 --jq .sha
gh api repos/astral-sh/setup-uv/commits/v6 --jq .sha
```

Remplacer `actions/checkout@v4` par `actions/checkout@<sha> # v4` et `astral-sh/setup-uv@v6` par `astral-sh/setup-uv@<sha> # v6`. Remplacer les images des services `postgres`/`redis` et l'image MinIO par leurs digests (Task 4 Step 2). La variable `vars.MINIO_IMAGE` n'est plus nécessaire une fois le digest écrit en dur : la retirer.

- [ ] **Step 4: Vérifier en local puis sur GitHub**

Run: `make ci`
Expected: toutes les cibles se terminent avec le code 0.

Pousser la branche, ouvrir la PR, vérifier que le job `checks` est vert.

- [ ] **Step 5: Commit**

```bash
git add Makefile .github/workflows/ci.yml
git commit -m "ci: lint, migrations, tests, couverture, audit des dépendances, secrets et image"
```

---

### Task 6: Documentation du socle

**Files:**
- Create: `README.md`, `docs/development.md`

**Interfaces:**
- Consumes: commandes et variables définies dans les Tasks 1–5.
- Produces: point d'entrée documentaire du dépôt.

**Acceptance Criteria:**
- Un développeur qui ne connaît pas le projet suit le README et obtient `/readyz` à 200 en local.
- La documentation liste chaque variable d'environnement, son rôle, sa valeur par défaut et si elle est secrète.
- Le README renvoie vers `docs/cadrage.md`, la spec `.internal/specs/2026-10-09-mvp-connaissance-design.md` et `docs/decisions/INDEX.md`.
- Aucune fonctionnalité non livrée n'est présentée comme disponible : le README indique explicitement que seul le socle L0 existe.

- [ ] **Step 1: Écrire `README.md`**

Contenu requis, dans cet ordre : titre « Plateforme Communautés TALAN » ; paragraphe d'état (« Lot L0 livré : socle technique. Aucune fonctionnalité métier n'est encore disponible. ») ; prérequis (Docker ≥ 24, uv ≥ 0.4, Make) ; démarrage rapide (les commandes exactes de Task 4 Step 6 jusqu'à `curl …/readyz`) ; commandes utiles (`make lint`, `make test`, `make test-integration`, `make ci`) ; URLs locales (application `:8000`, console MinIO `:9001`, Mailpit `:8025`) ; liens vers cadrage, spec, ADR et `docs/development.md`.

- [ ] **Step 2: Écrire `docs/development.md`**

Sections requises : organisation du code (arborescence de ce plan, rôle de `config/` et `core/`, conventions `selectors`/`services`/`policies` annoncées pour L1+) ; réglages par environnement (`dev`, `test`, `prod`) ; tableau des variables d'environnement :

| Variable | Rôle | Défaut | Secrète |
|---|---|---|---|
| `DJANGO_SETTINGS_MODULE` | Module de réglages | `config.settings.dev` (manage.py), `config.settings.prod` (image) | non |
| `DJANGO_SECRET_KEY` | Clé de signature Django | aucune (obligatoire) | **oui** |
| `DJANGO_ALLOWED_HOSTS` | Noms d'hôte acceptés | vide (refusé en prod) | non |
| `DJANGO_DEBUG` | Mode debug | `false` | non |
| `DATABASE_URL` | Connexion PostgreSQL | aucune (obligatoire) | **oui** (mot de passe) |
| `DB_CONN_MAX_AGE` | Durée de vie des connexions (s) | `60` | non |
| `REDIS_URL` | Cache, verrous, broker Celery | aucune (obligatoire) | **oui** si authentifié |
| `S3_BUCKET` | Bucket des documents | aucune (obligatoire) | non |
| `S3_ENDPOINT_URL` | Point d'accès S3-compatible | vide (AWS) | non |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | Identifiants du stockage | vide | **oui** |
| `S3_REGION` | Région | vide | non |
| `EMAIL_URL` | Serveur SMTP | `consolemail://` | **oui** si identifiants |
| `DEFAULT_FROM_EMAIL` | Expéditeur | `communautes@localhost` | non |
| `METRICS_TOKEN` | Jeton d'accès à `/metrics` (vide = désactivé) | vide | **oui** |
| `LOG_LEVEL` | Niveau de log | `INFO` | non |
| `LOG_JSON` | Logs JSON | `true` (`false` en dev) | non |

puis : migrations (jamais automatiques ; `docker compose run --rm migrate`) ; tests (unitaires, intégration stockage, couverture) ; observabilité (`/healthz`, `/readyz`, `/metrics` + jeton, `X-Request-ID`) ; mise à jour des digests d'images et des SHA d'actions (commandes de Task 4 Step 2 et Task 5 Step 3).

- [ ] **Step 3: Vérifier**

Run: `grep -n "L0" README.md && grep -c "|" docs/development.md`
Expected: la mention d'état L0 est présente ; le tableau des variables est présent.

Relire le README en suivant les commandes sur un clone propre (`git clone` dans un répertoire temporaire, `cp .env.example .env`, `docker compose up -d --build --wait`, `curl …/readyz`).

- [ ] **Step 4: Commit**

```bash
git add README.md docs/development.md
git commit -m "docs: README et guide de développement du socle"
```

---

## Couverture de la spécification (§ 3 — Lot L0)

| Exigence de la spec | Tâche |
|---|---|
| Settings `base/dev/test/prod` lus depuis l'environnement (`django-environ`) | Task 1 |
| `compose.yaml` : web, worker, beat, postgres 16, redis 7, minio, clamav, mailpit | Task 4 |
| Dockerfile multi-étapes non-root, `uv.lock`, `.env.example` | Tasks 1, 4 |
| CI GitHub Actions + `make ci` local | Task 5 |
| Contrôles CI : ruff check/format, pytest sur PostgreSQL réel, `makemigrations --check`, migrations depuis base vide, pip-audit, bandit, gitleaks, build image, trivy CRITICAL | Task 5 |
| Middleware `X-Request-ID`, logs JSON structlog | Task 2 |
| `/healthz`, `/readyz` | Task 1 |
| `/metrics` (django-prometheus) protégé par réseau/jeton | Tasks 3, 4 (blocage Nginx) |
| Celery (base des tâches asynchrones, fiabilité) | Task 3 |
| Stockage objet privé S3-compatible | Tasks 1, 4 |
| Nginx comme reverse proxy (cadrage § 5) | Task 4 |
| Acceptation : `docker compose up` rend l'application utilisable ; CI verte ; gitleaks sans fuite | Tasks 4, 5 |

**Écart assumé, à signaler** : la spec mentionne `make demo` dans l'acceptation de L0 ; les données de démonstration dépendent des modèles métier (L1+). La commande `seed_demo` est prévue en L11 (spec § 14) et enrichie à chaque lot ; L0 ne livre donc pas `make demo`.
