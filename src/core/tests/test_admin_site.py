import pytest
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import Group
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.roles import Role
from core.admin_site import SecureAdminSite

pytestmark = pytest.mark.django_db

ADMIN_INDEX = "/" + settings.DJANGO_ADMIN_PATH
USER_CHANGELIST = ADMIN_INDEX + "accounts/user/"


def verify(client, user):
    device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    code = f"{totp(device.bin_key, step=device.step, digits=device.digits):06d}"
    response = client.post(reverse("accounts:mfa_verify"), {"otp_token": code})
    assert response.status_code == 302


def test_default_admin_site_is_secure_and_mounted_on_configured_path():
    assert isinstance(admin.site, SecureAdminSite)
    assert reverse("admin:index") == ADMIN_INDEX


@pytest.mark.parametrize("path", [ADMIN_INDEX, USER_CHANGELIST, ADMIN_INDEX + "login/"])
def test_anonymous_gets_404(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", [ADMIN_INDEX, USER_CHANGELIST])
def test_employee_gets_404(client, make_user, path):
    employee = make_user("emp@example.com")
    employee.groups.add(Group.objects.get(name=Role.EMPLOYEE))
    client.force_login(employee)
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", [ADMIN_INDEX, USER_CHANGELIST])
def test_staff_without_verified_otp_gets_404(client, make_user, path):
    staff = make_user("staff@example.com", is_staff=True, is_superuser=True)
    client.force_login(staff)
    # Same answer as an unknown path: the admin location is not revealed.
    assert client.get(path).status_code == 404
    assert client.get(ADMIN_INDEX + "no-such-app/").status_code == 404


def test_staff_without_verified_otp_is_refused_by_site_itself(rf, make_user):
    staff = make_user("staff@example.com", is_staff=True, is_superuser=True)
    request = rf.get(ADMIN_INDEX)
    staff.is_verified = lambda: False
    request.user = staff
    assert not admin.site.has_permission(request)


@pytest.mark.parametrize("path", [ADMIN_INDEX, USER_CHANGELIST, ADMIN_INDEX + "audit/auditevent/"])
def test_verified_staff_gets_200(client, make_user, path):
    staff = make_user("staff@example.com", is_staff=True, is_superuser=True)
    client.force_login(staff)
    verify(client, staff)
    response = client.get(path)
    assert response.status_code == 200


def test_verified_non_staff_privileged_user_gets_404(client, make_user):
    user = make_user("fa@example.com")
    user.groups.add(Group.objects.get(name=Role.FUNCTIONAL_ADMIN))
    client.force_login(user)
    verify(client, user)
    assert client.get(ADMIN_INDEX).status_code == 404
