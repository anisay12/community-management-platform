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
        "AUDIT_IP_HASH_KEY": secrets.token_urlsafe(32),
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


def test_dev_settings_load():
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.dev",
        "DJANGO_SECRET_KEY": "dev-only",
        "DATABASE_URL": "postgres://u:p@localhost:5432/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "S3_BUCKET": "documents",
        "AUDIT_IP_HASH_KEY": "dev-only",
    }
    result = _manage("check", env=env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_settings_require_audit_ip_hash_key():
    env = _prod_env()
    env.pop("AUDIT_IP_HASH_KEY")
    env["DJANGO_READ_DOT_ENV"] = "0"  # a developer's .env must not re-supply the key
    result = _manage("check", env=env)
    assert result.returncode != 0
    assert "AUDIT_IP_HASH_KEY" in result.stderr


def test_settings_reject_unknown_auth_mode():
    result = _manage("check", env=_prod_env(AUTH_MODE="ldap"))
    assert result.returncode != 0
    assert "AUTH_MODE" in result.stderr


OIDC_ENV = {
    "OIDC_RP_CLIENT_ID": "client",
    "OIDC_RP_CLIENT_SECRET": secrets.token_urlsafe(16),
    "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://idp.example.com/authorize",
    "OIDC_OP_TOKEN_ENDPOINT": "https://idp.example.com/token",
    "OIDC_OP_USER_ENDPOINT": "https://idp.example.com/userinfo",
    "OIDC_OP_JWKS_ENDPOINT": "https://idp.example.com/jwks",
    "OIDC_OP_ISSUER": "https://idp.example.com/tenant/v2.0",
}
ENTRA_MULTI_TENANT = "https://login.microsoftonline.com/{}/oauth2/v2.0/authorize"
PRINT_AUTH_SETTINGS = (
    "from django.conf import settings; "
    "print(settings.AUTHENTICATION_BACKENDS); print(settings.ADMINS)"
)


def test_sso_modes_require_oidc_settings():
    for mode in ("mixed", "sso_only"):
        env = _prod_env(AUTH_MODE=mode, **{**OIDC_ENV, "OIDC_OP_JWKS_ENDPOINT": ""})
        env["DJANGO_READ_DOT_ENV"] = "0"
        result = _manage("check", env=env)
        assert result.returncode != 0
        assert "OIDC_OP_JWKS_ENDPOINT" in result.stderr


def test_sso_modes_require_the_issuer():
    for mode in ("mixed", "sso_only"):
        env = _prod_env(AUTH_MODE=mode, **{**OIDC_ENV, "OIDC_OP_ISSUER": ""})
        env["DJANGO_READ_DOT_ENV"] = "0"
        result = _manage("check", env=env)
        assert result.returncode != 0
        assert "ImproperlyConfigured" in result.stderr
        assert "OIDC_OP_ISSUER" in result.stderr


def test_multi_tenant_endpoints_require_a_tenant_pin():
    for segment in ("common", "organizations"):
        env = _prod_env(
            AUTH_MODE="mixed",
            **{**OIDC_ENV, "OIDC_OP_AUTHORIZATION_ENDPOINT": ENTRA_MULTI_TENANT.format(segment)},
        )
        env["DJANGO_READ_DOT_ENV"] = "0"
        result = _manage("check", env=env)
        assert result.returncode != 0
        assert "ImproperlyConfigured" in result.stderr
        assert "OIDC_ALLOWED_TENANT_ID" in result.stderr


def test_multi_tenant_endpoints_are_accepted_with_a_tenant_pin():
    env = _prod_env(
        AUTH_MODE="mixed",
        OIDC_ALLOWED_TENANT_ID="11111111-2222-3333-4444-555555555555",
        **{**OIDC_ENV, "OIDC_OP_AUTHORIZATION_ENDPOINT": ENTRA_MULTI_TENANT.format("common")},
    )
    env["DJANGO_READ_DOT_ENV"] = "0"
    result = _manage("check", env=env)
    assert result.returncode == 0, result.stdout + result.stderr


def test_sso_mode_enables_the_oidc_backend():
    env = _prod_env(
        AUTH_MODE="sso_only", DJANGO_ADMINS="Ops Team <ops@example.com>,b@example.com", **OIDC_ENV
    )
    result = _manage("shell", "-c", PRINT_AUTH_SETTINGS, env=env)
    assert result.returncode == 0, result.stderr
    assert "accounts.oidc.TalanOIDCBackend" in result.stdout
    assert "[('Ops Team', 'ops@example.com'), ('', 'b@example.com')]" in result.stdout


def test_local_mode_needs_no_oidc_settings():
    env = _prod_env(AUTH_MODE="local")
    env["DJANGO_READ_DOT_ENV"] = "0"
    result = _manage("shell", "-c", PRINT_AUTH_SETTINGS, env=env)
    assert result.returncode == 0, result.stderr
    assert "TalanOIDCBackend" not in result.stdout
    assert "[]" in result.stdout


def test_redis_cache_fails_fast():
    from config.settings import base

    assert base.CACHES["default"]["OPTIONS"] == {"socket_connect_timeout": 1, "socket_timeout": 1}
