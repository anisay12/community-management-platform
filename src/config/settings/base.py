from datetime import timedelta
from email.utils import getaddresses
from pathlib import Path

import environ
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured

from core.logging import build_logging, configure_structlog

BASE_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = BASE_DIR / "src"

env = environ.Env()
if env.bool("DJANGO_READ_DOT_ENV", default=True) and (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    # django.contrib.admin with core.admin_site.SecureAdminSite as the default site.
    "core.apps.SecureAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_prometheus",
    "axes",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    # Always installed; its URLs and backend are only enabled in the SSO modes.
    "mozilla_django_oidc",
    "core",
    "taxonomy",
    "organizations",
    "accounts",
    "audit",
    "communities",
    "notifications",
    "posts",
    "documents",
]

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "core.middleware.RequestIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "core.middleware.RequestUserContextMiddleware",
    # Saved language and time zone; needs AuthenticationMiddleware and LocaleMiddleware.
    "core.middleware.UserPreferencesMiddleware",
    "core.middleware.ActivityMiddleware",
    # Needs OTPMiddleware (user.is_verified) above it.
    "accounts.middleware.MFARequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
    # Must stay last: turns lockouts raised during authentication into the 429 page.
    "axes.middleware.AxesMiddleware",
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
                "django.template.context_processors.i18n",
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
        # Fail fast when Redis is slow or down, so the rate limiter fails open quickly.
        "OPTIONS": {"socket_connect_timeout": 1, "socket_timeout": 1},
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

AUTH_USER_MODEL = "accounts.User"
# Email uniqueness is enforced case-insensitively by a functional UniqueConstraint on
# Lower("email"), which the auth system check cannot see.
SILENCED_SYSTEM_CHECKS = ["auth.E003", "auth.W004"]

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("fr", "Français")]
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

# Number of reverse proxies in front of the app; 0 means X-Forwarded-For is not trusted.
NUM_PROXIES = env.int("NUM_PROXIES", default=0)

# Authentication ----------------------------------------------------------------
AUTH_MODES = ("local", "mixed", "sso_only")
AUTH_MODE = env("AUTH_MODE", default="local")
if AUTH_MODE not in AUTH_MODES:
    raise ImproperlyConfigured(f"AUTH_MODE must be one of {', '.join(AUTH_MODES)}.")
# Emergency local account that may sign in with a password whatever AUTH_MODE is.
BREAK_GLASS_EMAIL = env("BREAK_GLASS_EMAIL", default="")
# Public base URL used to build links in emails (never derived from the Host header).
SITE_URL = env("SITE_URL", default="http://localhost:8000")

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "accounts.backends.EmailBackend",
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Single sign-on (OpenID Connect, mozilla-django-oidc), enabled in the mixed and
# sso_only modes. The provider name keys ExternalIdentity rows.
OIDC_PROVIDER_NAME = env("OIDC_PROVIDER_NAME", default="entra")
OIDC_RP_SIGN_ALGO = "RS256"
OIDC_RP_SCOPES = "openid email profile"
OIDC_USE_PKCE = True
OIDC_TIMEOUT = 10
OIDC_CALLBACK_CLASS = "accounts.oidc.TalanOIDCCallbackView"
OIDC_REQUIRED_SETTINGS = (
    "OIDC_RP_CLIENT_ID",
    "OIDC_RP_CLIENT_SECRET",
    "OIDC_OP_AUTHORIZATION_ENDPOINT",
    "OIDC_OP_TOKEN_ENDPOINT",
    "OIDC_OP_USER_ENDPOINT",
    "OIDC_OP_JWKS_ENDPOINT",
    # Exact expected ``iss`` of ID tokens: pins the tenant whose email claims are trusted.
    "OIDC_OP_ISSUER",
)
OIDC_OP_ENDPOINT_SETTINGS = (
    "OIDC_OP_AUTHORIZATION_ENDPOINT",
    "OIDC_OP_TOKEN_ENDPOINT",
    "OIDC_OP_USER_ENDPOINT",
    "OIDC_OP_JWKS_ENDPOINT",
)
# Optional: when set, the ID token's ``tid`` claim must match (Entra tenant ID).
OIDC_ALLOWED_TENANT_ID = env("OIDC_ALLOWED_TENANT_ID", default="").strip()
if AUTH_MODE != "local":
    _oidc = {name: env(name, default="").strip() for name in OIDC_REQUIRED_SETTINGS}
    _missing = [name for name, value in _oidc.items() if not value]
    if _missing:
        raise ImproperlyConfigured(
            f"AUTH_MODE={AUTH_MODE} requires these settings: {', '.join(_missing)}."
        )
    # Multi-tenant endpoints accept tokens from any tenant; accounts are linked by email,
    # so a foreign tenant could claim a local address unless the tenant is pinned.
    _multi_tenant = [
        name
        for name in OIDC_OP_ENDPOINT_SETTINGS
        if "/common/" in _oidc[name] or "/organizations/" in _oidc[name]
    ]
    if _multi_tenant and not OIDC_ALLOWED_TENANT_ID:
        raise ImproperlyConfigured(
            f"{', '.join(_multi_tenant)} use a multi-tenant endpoint: "
            "set OIDC_ALLOWED_TENANT_ID or use the tenant-specific endpoints."
        )
    vars().update(_oidc)
    AUTHENTICATION_BACKENDS.append("accounts.oidc.TalanOIDCBackend")

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "accounts:login"
PASSWORD_RESET_TIMEOUT = 3600
ACCOUNT_ACTIVATION_TIMEOUT = 72 * 3600

# 8 h of inactivity: ActivityMiddleware re-saves the session at most every 5 minutes.
SESSION_COOKIE_AGE = 8 * 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# Brute-force protection (django-axes): lock a (email, IP) pair after 5 failures.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_USERNAME_FORM_FIELD = "username"
# Lowercases the email so case variants share one failure counter.
AXES_USERNAME_CALLABLE = "accounts.backends.axes_username"
# Single sign-on callbacks are not password attempts and are not counted.
AXES_WHITELIST_CALLABLE = "accounts.backends.axes_skip_non_password_attempt"
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_CALLABLE = "core.views_errors.axes_lockout"
# Same proxy-aware client IP as the audit log; takes precedence over the ipware settings.
AXES_CLIENT_IP_CALLABLE = "core.context.client_ip"
AXES_IPWARE_PROXY_COUNT = NUM_PROXIES
AXES_IPWARE_META_PRECEDENCE_ORDER = ["HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"]
AXES_ENABLE_ADMIN = False

# Second factor (django-otp): mandatory TOTP for privileged users (accounts.policies).
OTP_TOTP_ISSUER = "Talan Communities"
MFA_MAX_FAILURES = 5
MFA_FAILURE_WINDOW = timedelta(minutes=15)

# Hidden Django admin: only reachable by verified staff, 404 for everyone else.
DJANGO_ADMIN_PATH = env("DJANGO_ADMIN_PATH", default="django-admin/")
if not DJANGO_ADMIN_PATH.endswith("/") or DJANGO_ADMIN_PATH.startswith("/"):
    raise ImproperlyConfigured("DJANGO_ADMIN_PATH must end with '/' and not start with '/'.")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

vars().update(env.email_url("EMAIL_URL", default="consolemail://"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="communautes@localhost")
# Recipients of operational alerts (break-glass sign-in): "Name <email>,other@example.com".
ADMINS = [entry for entry in getaddresses([env("DJANGO_ADMINS", default="")]) if entry[1]]
SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_SUBJECT_PREFIX = "[Talan Communities] "

METRICS_TOKEN = env("METRICS_TOKEN", default="")

AUDIT_IP_HASH_KEY = env("AUDIT_IP_HASH_KEY")
AUDIT_RETENTION_DAYS = env.int("AUDIT_RETENTION_DAYS", default=365)

# Personal data: deactivated accounts are anonymized after 3 years; exports live 7 days.
ACCOUNT_ANONYMIZE_AFTER_DAYS = env.int("ACCOUNT_ANONYMIZE_AFTER_DAYS", default=1095)
DATA_EXPORT_TTL_DAYS = 7

LOG_LEVEL = env("LOG_LEVEL", default="INFO")
LOG_JSON = env.bool("LOG_JSON", default=True)
LOGGING = build_logging(level=LOG_LEVEL, json=LOG_JSON)
configure_structlog()

CELERY_BROKER_URL = REDIS_URL
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_TASK_TIME_LIMIT = 300
CELERY_TIMEZONE = TIME_ZONE

CELERY_BEAT_SCHEDULE = {
    "audit-purge": {
        "task": "audit.tasks.purge_audit_events",
        "schedule": crontab(hour=3, minute=15),
    },
    "accounts-purge-expired-exports": {
        "task": "accounts.tasks.purge_expired_exports",
        "schedule": crontab(hour=3, minute=30),
    },
    "accounts-anonymize-expired-accounts": {
        "task": "accounts.tasks.anonymize_expired_accounts",
        "schedule": crontab(hour=3, minute=45),
    },
    "posts-verify-counters": {
        "task": "posts.tasks.verify_counters",
        "schedule": crontab(hour=4, minute=0),
    },
}

# Posts: hourly write limits per user, automatic hiding after N open reports, pinned posts.
POSTS_RATE_LIMITS = {"post": 10, "comment": 60, "reaction": 120}
POSTS_REPORT_AUTOHIDE_THRESHOLD = 3
POSTS_PIN_LIMIT = 3
# Bookmark collections one user may keep (bounds the bookmarks page).
POSTS_MAX_BOOKMARK_COLLECTIONS = 50

# Documents (L5): upload limit, ClamAV daemon, private serving and retention.
DOCUMENT_MAX_UPLOAD_BYTES = env.int("DOCUMENT_MAX_UPLOAD_BYTES", default=100 * 1024 * 1024)
CLAMAV_HOST = env("CLAMAV_HOST", default="clamav")
CLAMAV_PORT = env.int("CLAMAV_PORT", default=3310)
CLAMAV_TIMEOUT_SECONDS = env.int("CLAMAV_TIMEOUT_SECONDS", default=60)
# Internal Nginx location that relays reads to the private object store (ADR-0001).
DOCUMENT_PROTECTED_PREFIX = "/_protected/"
DOWNLOAD_LOG_RETENTION_DAYS = env.int("DOWNLOAD_LOG_RETENTION_DAYS", default=365)
# Development only: with DEBUG, Django streams document files itself instead of answering
# X-Accel-Redirect. Set to false to try the Nginx path locally (never used without DEBUG).
DOCUMENT_DEV_STREAMING = env.bool("DOCUMENT_DEV_STREAMING", default=True)
