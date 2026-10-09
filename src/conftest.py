import importlib

import pytest
from celery import _state as celery_state
from django.contrib.auth.models import Group
from django.db.models import F
from django.urls import clear_url_caches, reverse
from django.utils.text import slugify
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import User
from accounts.roles import Role
from communities.models import Community, CommunityCategory, CommunityMembership

PASSWORD = "correct-horse-battery-staple"  # noqa: S105  # test fixture


@pytest.fixture(autouse=True)
def _reset_celery_task_join_flag():
    """Eager Celery tasks run from several threads at once (concurrency tests) can leave
    Celery's process-global "join will block" flag set, because ``denied_join_result`` is
    not thread-safe; later tests calling ``result.get()`` would then fail. Reset it around
    every test."""
    celery_state._set_task_join_will_block(False)
    yield
    celery_state._set_task_join_will_block(False)


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


OIDC_TEST_SETTINGS = {
    "OIDC_RP_CLIENT_ID": "test-client-id",
    "OIDC_RP_CLIENT_SECRET": "test-only-client-secret",
    "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://idp.example.com/authorize",
    "OIDC_OP_TOKEN_ENDPOINT": "https://idp.example.com/token",
    "OIDC_OP_USER_ENDPOINT": "https://idp.example.com/userinfo",
    "OIDC_OP_JWKS_ENDPOINT": "https://idp.example.com/jwks",
    "OIDC_OP_ISSUER": "https://idp.example.com/tenant/v2.0",
    "OIDC_ALLOWED_TENANT_ID": "",
}
OIDC_BACKEND = "accounts.oidc.TalanOIDCBackend"


@pytest.fixture
def oidc_settings(settings):
    """Identity provider settings (never contacted: tests mock the provider)."""
    for name, value in OIDC_TEST_SETTINGS.items():
        setattr(settings, name, value)
    return settings


def _reload_urls(settings):
    """Re-import the URLconf, which mounts the OIDC views only in SSO modes."""
    importlib.reload(importlib.import_module(settings.ROOT_URLCONF))
    clear_url_caches()


@pytest.fixture
def use_auth_mode(oidc_settings):
    """Switch AUTH_MODE as the settings module would, including the OIDC URLs."""
    original_mode = oidc_settings.AUTH_MODE
    local_backends = [b for b in oidc_settings.AUTHENTICATION_BACKENDS if b != OIDC_BACKEND]

    def _use(mode):
        oidc_settings.AUTH_MODE = mode
        oidc_settings.AUTHENTICATION_BACKENDS = (
            local_backends if mode == "local" else [*local_backends, OIDC_BACKEND]
        )
        _reload_urls(oidc_settings)

    yield _use
    oidc_settings.AUTH_MODE = original_mode
    _reload_urls(oidc_settings)


# Communities ----------------------------------------------------------------------
@pytest.fixture
def category(db):
    return CommunityCategory.objects.create(name="Test category", slug="test-category")


@pytest.fixture
def make_community(category):
    def _make(name="Python guild", **extra):
        extra.setdefault("tagline", "Everything Python")
        extra.setdefault("category", category)
        extra.setdefault("slug", slugify(name))
        return Community.objects.create(name=name, **extra)

    return _make


@pytest.fixture
def add_member():
    """Add a membership directly, keeping ``Community.member_count`` in sync."""

    def _add(community, user, role=CommunityMembership.Role.MEMBER):
        membership = CommunityMembership.objects.create(community=community, user=user, role=role)
        Community.objects.filter(pk=community.pk).update(member_count=F("member_count") + 1)
        community.member_count += 1
        return membership

    return _add
