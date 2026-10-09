import pytest
from django.contrib.auth.models import Group
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import User
from accounts.roles import Role

PASSWORD = "correct-horse-battery-staple"  # noqa: S105  # test fixture


@pytest.fixture
def make_user(db):
    def _make(email="alice@example.com", *, status=User.Status.ACTIVE, password=PASSWORD, **extra):
        extra.setdefault("first_name", "Alice")
        extra.setdefault("last_name", "Doe")
        return User.objects.create_user(email, password=password, status=status, **extra)

    return _make


@pytest.fixture
def active_user(make_user):
    return make_user()


@pytest.fixture
def verified_login():
    """Log ``user`` into ``client`` with a session verified by a confirmed TOTP device."""

    def _login(client, user):
        client.force_login(user)
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
        code = f"{totp(device.bin_key, step=device.step, digits=device.digits):06d}"
        response = client.post(reverse("accounts:mfa_verify"), {"otp_token": code})
        assert response.status_code == 302  # noqa: S101
        return client

    return _login


@pytest.fixture
def functional_admin(make_user):
    user = make_user("fadmin@example.com", first_name="Fiona", last_name="Admin")
    user.groups.add(Group.objects.get(name=Role.FUNCTIONAL_ADMIN))
    return user
