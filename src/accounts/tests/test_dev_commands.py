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
    assert list(AuditEvent.objects.values_list("action", flat=True)) == ["user.dev_admin_created"]
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


# create_admin (production bootstrap) -----------------------------------------------------

ADMIN_PASSWORD_VAR = "BOOTSTRAP_ADMIN_PASSWORD"  # variable name, not a secret


@pytest.fixture
def admin_password(monkeypatch):
    monkeypatch.setenv(ADMIN_PASSWORD_VAR, PASSWORD)


def create_admin(email="root@example.com", **options):
    call_command("create_admin", email=email, password_from_env=ADMIN_PASSWORD_VAR, **options)


def test_create_admin_bootstraps_an_audited_technical_admin(settings, admin_password, capsys):
    settings.DEBUG = False
    create_admin(email="Root@Example.com", first_name="Rita", last_name="Root")
    user = User.objects.get(email="root@example.com")
    assert user.status == User.Status.ACTIVE
    assert user.is_superuser and user.is_staff
    assert user.check_password(PASSWORD)
    assert (user.first_name, user.last_name) == ("Rita", "Root")
    assert has_role(user, Role.TECHNICAL_ADMIN)
    event = AuditEvent.objects.get()
    assert event.action == "user.admin_bootstrapped"
    assert event.actor is None
    assert event.target_id == str(user.public_id)
    assert event.changes["force_additional"] is False
    assert "MFA enrollment is required" in capsys.readouterr().out


def test_create_admin_refuses_when_an_active_superuser_exists(admin_password, make_user):
    make_user("first@example.com", is_superuser=True, is_staff=True)
    with pytest.raises(CommandError, match="--force-additional"):
        create_admin()
    assert not User.objects.filter(email="root@example.com").exists()
    assert not AuditEvent.objects.filter(action="user.admin_bootstrapped").exists()


def test_create_admin_ignores_inactive_superusers(admin_password, make_user):
    make_user("gone@example.com", is_superuser=True, status=User.Status.DEACTIVATED)
    create_admin()
    assert User.objects.filter(email="root@example.com", is_superuser=True).exists()


def test_create_admin_force_additional_is_audited(admin_password, make_user):
    make_user("first@example.com", is_superuser=True, is_staff=True)
    create_admin(force_additional=True)
    event = AuditEvent.objects.get(action="user.admin_bootstrapped")
    assert event.changes["force_additional"] is True


def test_create_admin_rejects_existing_email(admin_password, make_user):
    make_user("root@example.com")
    with pytest.raises(CommandError, match="already exists"):
        create_admin(email="ROOT@example.com", force_additional=True)


def test_create_admin_rejects_missing_or_weak_password(monkeypatch):
    monkeypatch.delenv(ADMIN_PASSWORD_VAR, raising=False)
    with pytest.raises(CommandError, match=ADMIN_PASSWORD_VAR):
        create_admin()
    monkeypatch.setenv(ADMIN_PASSWORD_VAR, "short")
    with pytest.raises(CommandError):
        create_admin()
    assert not User.objects.exists()


def test_create_admin_rejects_empty_names(admin_password):
    with pytest.raises(CommandError, match="names"):
        create_admin(first_name=" ")


def test_create_admin_prompts_for_a_password(monkeypatch):
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    call_command("create_admin", email="root@example.com")
    assert User.objects.get(email="root@example.com").check_password(PASSWORD)


def test_create_admin_rejects_mismatched_prompts(monkeypatch):
    answers = iter([PASSWORD, PASSWORD + "x"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    with pytest.raises(CommandError, match="do not match"):
        call_command("create_admin", email="root@example.com")
