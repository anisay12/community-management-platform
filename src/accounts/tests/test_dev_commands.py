import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import User
from accounts.roles import Role, has_role
from audit.models import AuditEvent

pytestmark = pytest.mark.django_db

PASSWORD = "dev-admin-password-123"  # test fixture


def test_refuses_when_debug_is_false(settings, monkeypatch):
    settings.DEBUG = False
    monkeypatch.setenv("DEV_ADMIN_PASSWORD", PASSWORD)
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("create_dev_admin", email="dev@example.com", password_from_env=True)
    assert not User.objects.filter(email="dev@example.com").exists()


def test_creates_active_superuser_with_functional_admin_role(settings, monkeypatch, capsys):
    settings.DEBUG = True
    monkeypatch.setenv("DEV_ADMIN_PASSWORD", PASSWORD)
    call_command("create_dev_admin", email="Dev@Example.com", password_from_env=True)
    user = User.objects.get(email="dev@example.com")
    assert user.is_active
    assert user.is_staff
    assert user.is_superuser
    assert user.check_password(PASSWORD)
    assert has_role(user, Role.FUNCTIONAL_ADMIN)
    assert AuditEvent.objects.filter(action="user.dev_admin_created").exists()
    assert "two-factor" in capsys.readouterr().out


def test_prompts_for_a_password(settings, monkeypatch):
    settings.DEBUG = True
    monkeypatch.setattr("getpass.getpass", lambda prompt="": PASSWORD)
    call_command("create_dev_admin", email="dev@example.com")
    assert User.objects.get(email="dev@example.com").check_password(PASSWORD)


def test_rejects_missing_env_password(settings, monkeypatch):
    settings.DEBUG = True
    monkeypatch.delenv("DEV_ADMIN_PASSWORD", raising=False)
    with pytest.raises(CommandError, match="DEV_ADMIN_PASSWORD"):
        call_command("create_dev_admin", email="dev@example.com", password_from_env=True)


def test_rejects_weak_password(settings, monkeypatch):
    settings.DEBUG = True
    monkeypatch.setenv("DEV_ADMIN_PASSWORD", "short")
    with pytest.raises(CommandError):
        call_command("create_dev_admin", email="dev@example.com", password_from_env=True)
    assert not User.objects.filter(email="dev@example.com").exists()


def test_rejects_existing_account(settings, monkeypatch, make_user):
    settings.DEBUG = True
    make_user("dev@example.com")
    monkeypatch.setenv("DEV_ADMIN_PASSWORD", PASSWORD)
    with pytest.raises(CommandError, match="already exists"):
        call_command("create_dev_admin", email="DEV@example.com", password_from_env=True)
