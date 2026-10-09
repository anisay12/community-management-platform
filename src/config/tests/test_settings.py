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
