import pytest
from django.conf import settings
from django.contrib.auth.models import Group
from django.core import mail

from accounts.admin_forms import UserAddForm
from accounts.models import User
from accounts.roles import Role, has_role
from audit.models import AuditEvent

pytestmark = pytest.mark.django_db

ADMIN_USERS = "/" + settings.DJANGO_ADMIN_PATH + "accounts/user/"


@pytest.fixture
def root(make_user):
    return make_user("root@example.com", is_staff=True, is_superuser=True)


@pytest.fixture
def admin_site_client(client, root, verified_login):
    return verified_login(client, root)


def test_add_form_creates_audited_pending_user_with_unusable_password(
    root, django_capture_on_commit_callbacks
):
    form = UserAddForm(
        data={"email": "New.User@Example.com", "first_name": "New", "last_name": "User"}
    )
    assert form.is_valid(), form.errors
    with django_capture_on_commit_callbacks(execute=True):
        user = form.create(actor=root)
    user.refresh_from_db()
    assert user.email == "new.user@example.com"
    assert user.status == User.Status.PENDING
    assert not user.has_usable_password()
    assert has_role(user, Role.EMPLOYEE)
    event = AuditEvent.objects.get(action="user.created", target_id=str(user.public_id))
    assert event.actor == root
    assert len(mail.outbox) == 1


def test_add_form_rejects_duplicate_email_in_other_case():
    User.objects.create_user("bob@example.com", first_name="B", last_name="B")
    form = UserAddForm(data={"email": "BOB@example.com", "first_name": "B", "last_name": "B"})
    assert not form.is_valid()
    assert "email" in form.errors


def test_add_form_requires_names():
    form = UserAddForm(data={"email": "new@example.com", "first_name": " ", "last_name": ""})
    assert not form.is_valid()
    assert "first_name" in form.errors
    assert "last_name" in form.errors


def test_admin_add_view_creates_through_the_service(
    admin_site_client, root, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = admin_site_client.post(
            ADMIN_USERS + "add/",
            {"email": "Added@Example.com", "first_name": "Ada", "last_name": "Added"},
        )
    assert response.status_code == 302
    user = User.objects.get(email="added@example.com")
    assert user.status == User.Status.PENDING
    assert not user.has_usable_password()
    assert (
        AuditEvent.objects.filter(
            action="user.created", target_id=str(user.public_id), actor=root
        ).count()
        == 1
    )
    assert [message.to for message in mail.outbox] == [["added@example.com"]]


def test_admin_change_view_cannot_change_status_or_privileges(admin_site_client, make_user):
    target = make_user("bob@example.com", first_name="Bob", last_name="Ray")
    auditor = Group.objects.get(name=Role.AUDITOR)
    response = admin_site_client.post(
        f"{ADMIN_USERS}{target.pk}/change/",
        {
            "email": "bob@example.com",
            "first_name": "Bob",
            "last_name": "Ray",
            "status": User.Status.SUSPENDED,
            "deactivated_at": "2026-01-01 00:00:00",
            "is_staff": "on",
            "is_superuser": "on",
            "groups": [auditor.pk],
        },
    )
    assert response.status_code == 302
    target.refresh_from_db()
    assert target.status == User.Status.ACTIVE
    assert target.deactivated_at is None
    assert not target.is_staff
    assert not target.is_superuser
    # Group edits remain possible and are audited by the m2m signal.
    assert has_role(target, Role.AUDITOR)
    assert AuditEvent.objects.filter(target_id=str(target.public_id), action="user.roles_changed")


def test_admin_change_form_shows_status_and_privileges_read_only(admin_site_client, make_user):
    target = make_user("bob@example.com")
    form = admin_site_client.get(f"{ADMIN_USERS}{target.pk}/change/").context["adminform"].form
    for name in ("status", "is_staff", "is_superuser", "activated_at", "last_seen_at"):
        assert name not in form.fields
    assert "groups" in form.fields
