import uuid

import pytest
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts.models import User
from accounts.roles import Role, user_roles
from audit.models import AuditEvent
from organizations.models import Employment, OrganizationUnit

pytestmark = pytest.mark.django_db

HEADER = "email,first_name,last_name,unit_code,manager_email"


@pytest.fixture
def target(make_user):
    user = make_user("bob@example.com", first_name="Bob", last_name="Ray")
    user.groups.add(Group.objects.get(name=Role.EMPLOYEE))
    return user


@pytest.fixture
def admin_client(client, functional_admin, verified_login):
    return verified_login(client, functional_admin)


def manage_requests(target):
    detail = reverse("manage:user_detail", args=[target.public_id])
    return [
        ("get", reverse("manage:user_list"), {}),
        ("get", reverse("manage:user_create"), {}),
        ("post", reverse("manage:user_create"), {"email": "x@example.com"}),
        ("get", detail, {}),
        ("post", reverse("manage:user_status", args=[target.public_id]), {"action": "suspend"}),
        ("post", reverse("manage:user_roles", args=[target.public_id]), {"roles": ["auditor"]}),
        ("get", reverse("manage:user_import"), {}),
        ("post", reverse("manage:user_import"), {}),
    ]


def test_urls_are_mounted_under_manage(target):
    assert reverse("manage:user_list") == "/manage/users/"
    assert reverse("manage:user_detail", args=[target.public_id]) == (
        f"/manage/users/{target.public_id}/"
    )


@pytest.mark.parametrize("index", range(8))
def test_employee_gets_404_everywhere(client, make_user, target, index):
    employee = make_user("emp@example.com")
    employee.groups.add(Group.objects.get(name=Role.EMPLOYEE))
    client.force_login(employee)
    method, url, data = manage_requests(target)[index]
    assert getattr(client, method)(url, data).status_code == 404
    target.refresh_from_db()
    assert target.status == User.Status.ACTIVE
    assert user_roles(target) == {"employee"}


@pytest.mark.parametrize("index", range(8))
def test_anonymous_gets_404_everywhere(client, target, index):
    method, url, data = manage_requests(target)[index]
    assert getattr(client, method)(url, data).status_code == 404


def test_functional_admin_without_verified_mfa_is_sent_to_mfa(client, functional_admin):
    client.force_login(functional_admin)
    response = client.get(reverse("manage:user_list"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:mfa_setup"))


@pytest.mark.parametrize(
    "name", ["manage:user_list", "manage:user_create", "manage:user_import", "manage:user_detail"]
)
def test_verified_functional_admin_gets_200(admin_client, target, name):
    args = [target.public_id] if name == "manage:user_detail" else []
    assert admin_client.get(reverse(name, args=args)).status_code == 200


def test_verified_superuser_gets_200(client, make_user, verified_login):
    root = make_user("root@example.com", is_superuser=True)
    verified_login(client, root)
    assert client.get(reverse("manage:user_list")).status_code == 200


def test_unknown_user_is_404(admin_client):
    assert admin_client.get(reverse("manage:user_detail", args=[uuid.uuid4()])).status_code == 404


def test_status_and_roles_reject_get(admin_client, target):
    assert (
        admin_client.get(reverse("manage:user_status", args=[target.public_id])).status_code == 405
    )
    assert (
        admin_client.get(reverse("manage:user_roles", args=[target.public_id])).status_code == 405
    )


# List -----------------------------------------------------------------------------


def test_list_query_count_is_bounded(admin_client, make_user, django_assert_max_num_queries):
    unit = OrganizationUnit.objects.create(code="ENG", name="Engineering")
    employee = Group.objects.get(name=Role.EMPLOYEE)
    for i in range(25):
        user = make_user(f"user{i:02d}@example.com", last_name=f"Name{i:02d}")
        user.groups.add(employee)
        Employment.objects.create(user=user, unit=unit)
    admin_client.get(reverse("manage:user_list"))  # Warm the session / activity tracking.
    with django_assert_max_num_queries(8):
        response = admin_client.get(reverse("manage:user_list"))
    assert response.status_code == 200
    assert len(response.context["page_obj"].object_list) == 20
    assert response.context["page_obj"].paginator.count == 26


def test_list_search_and_status_filter(admin_client, make_user, target):
    make_user("carol@example.com", first_name="Carol", last_name="Zed", status=User.Status.PENDING)
    response = admin_client.get(reverse("manage:user_list"), {"q": "carol"})
    assert [u.email for u in response.context["page_obj"]] == ["carol@example.com"]
    response = admin_client.get(reverse("manage:user_list"), {"status": "pending"})
    assert [u.email for u in response.context["page_obj"]] == ["carol@example.com"]
    response = admin_client.get(reverse("manage:user_list"), {"q": "RAY"})
    assert [u.email for u in response.context["page_obj"]] == ["bob@example.com"]


def test_list_ignores_unknown_status(admin_client, target):
    response = admin_client.get(reverse("manage:user_list"), {"status": "bogus"})
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 2


# Create ---------------------------------------------------------------------------


def test_create_user_sends_one_activation_email_and_audits(
    admin_client, mailoutbox, django_capture_on_commit_callbacks
):
    unit = OrganizationUnit.objects.create(code="ENG", name="Engineering")
    with django_capture_on_commit_callbacks(execute=True):
        response = admin_client.post(
            reverse("manage:user_create"),
            {
                "email": "new@example.com",
                "first_name": "New",
                "last_name": "Person",
                "unit": unit.pk,
                "manager_email": "",
                "roles": ["employee"],
            },
        )
    user = User.objects.get(email="new@example.com")
    assert response.status_code == 302
    assert response["Location"] == reverse("manage:user_detail", args=[user.public_id])
    assert [m.to for m in mailoutbox] == [["new@example.com"]]
    assert AuditEvent.objects.filter(action="user.created", target_id=str(user.public_id)).count()
    assert user.employment.unit == unit


def test_create_user_shows_inline_errors_and_summary(admin_client, target):
    response = admin_client.post(
        reverse("manage:user_create"),
        {"email": "bob@example.com", "first_name": "", "last_name": "X", "roles": ["employee"]},
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert 'href="#id_first_name"' in content
    assert 'href="#id_email"' in content
    assert 'class="error-summary"' in content
    assert User.objects.filter(email="bob@example.com").count() == 1


def test_create_user_with_unknown_manager_is_an_inline_error(admin_client):
    response = admin_client.post(
        reverse("manage:user_create"),
        {
            "email": "new@example.com",
            "first_name": "N",
            "last_name": "P",
            "manager_email": "ghost@example.com",
            "roles": ["employee"],
        },
    )
    assert response.status_code == 200
    assert response.context["form"].errors["manager_email"]
    assert not User.objects.filter(email="new@example.com").exists()


def test_create_user_with_manager(admin_client, make_user):
    unit = OrganizationUnit.objects.create(code="ENG", name="Engineering")
    boss = make_user("boss@example.com")
    admin_client.post(
        reverse("manage:user_create"),
        {
            "email": "new@example.com",
            "first_name": "N",
            "last_name": "P",
            "unit": unit.pk,
            "manager_email": "BOSS@example.com",
            "roles": ["employee"],
        },
    )
    assert User.objects.get(email="new@example.com").employment.manager == boss


def test_create_user_manager_without_unit_is_an_inline_error(admin_client, make_user):
    make_user("boss@example.com")
    response = admin_client.post(
        reverse("manage:user_create"),
        {
            "email": "new@example.com",
            "first_name": "N",
            "last_name": "P",
            "manager_email": "boss@example.com",
            "roles": ["employee"],
        },
    )
    assert response.context["form"].errors["unit"]
    assert not User.objects.filter(email="new@example.com").exists()


def test_create_user_forbidden_role_is_reported(admin_client):
    response = admin_client.post(
        reverse("manage:user_create"),
        {
            "email": "new@example.com",
            "first_name": "N",
            "last_name": "P",
            "roles": ["technical_admin"],
        },
    )
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    assert not User.objects.filter(email="new@example.com").exists()


# Status ---------------------------------------------------------------------------


def test_suspending_a_logged_in_user_ends_their_session(admin_client, target):
    victim = Client()
    victim.force_login(target)
    assert victim.get(reverse("home")).wsgi_request.user.is_authenticated
    response = admin_client.post(
        reverse("manage:user_status", args=[target.public_id]), {"action": "suspend"}
    )
    assert response.status_code == 302
    target.refresh_from_db()
    assert target.status == User.Status.SUSPENDED
    assert not victim.get(reverse("home")).wsgi_request.user.is_authenticated


@pytest.mark.parametrize(
    ("status", "action", "expected"),
    [
        (User.Status.SUSPENDED, "reactivate", User.Status.ACTIVE),
        (User.Status.ACTIVE, "deactivate", User.Status.DEACTIVATED),
        (User.Status.PENDING, "resend_activation", User.Status.PENDING),
    ],
)
def test_status_actions(admin_client, make_user, status, action, expected):
    user = make_user("x@example.com", status=status)
    response = admin_client.post(
        reverse("manage:user_status", args=[user.public_id]), {"action": action}, follow=True
    )
    user.refresh_from_db()
    assert user.status == expected
    assert [m.level_tag for m in response.context["messages"]] == ["success"]


def test_invalid_transition_is_shown_as_error_message(admin_client, target):
    response = admin_client.post(
        reverse("manage:user_status", args=[target.public_id]),
        {"action": "reactivate"},
        follow=True,
    )
    assert [m.level_tag for m in response.context["messages"]] == ["error"]


def test_admin_cannot_suspend_themselves(admin_client, functional_admin):
    response = admin_client.post(
        reverse("manage:user_status", args=[functional_admin.public_id]),
        {"action": "suspend"},
        follow=True,
    )
    functional_admin.refresh_from_db()
    assert functional_admin.status == User.Status.ACTIVE
    assert [m.level_tag for m in response.context["messages"]] == ["error"]


def test_unknown_status_action_is_400(admin_client, target):
    response = admin_client.post(
        reverse("manage:user_status", args=[target.public_id]), {"action": "delete"}
    )
    assert response.status_code == 400


# Roles ----------------------------------------------------------------------------


def test_changing_roles_writes_one_event(admin_client, target):
    seen = AuditEvent.objects.filter(action="user.roles_changed").values_list("pk", flat=True)
    seen = list(seen)
    response = admin_client.post(
        reverse("manage:user_roles", args=[target.public_id]),
        {"roles": ["employee", "community_creator"]},
    )
    assert response.status_code == 302
    [event] = AuditEvent.objects.filter(action="user.roles_changed").exclude(pk__in=seen)
    assert event.target_id == str(target.public_id)
    assert event.changes == {"before": ["employee"], "after": ["community_creator", "employee"]}


def test_functional_admin_cannot_grant_technical_admin(admin_client, target):
    response = admin_client.post(
        reverse("manage:user_roles", args=[target.public_id]),
        {"roles": ["employee", "technical_admin"]},
        follow=True,
    )
    assert user_roles(User.objects.get(pk=target.pk)) == {"employee"}
    assert [m.level_tag for m in response.context["messages"]] == ["error"]


def test_invalid_role_is_rejected(admin_client, target):
    response = admin_client.post(
        reverse("manage:user_roles", args=[target.public_id]), {"roles": ["god"]}, follow=True
    )
    assert user_roles(User.objects.get(pk=target.pk)) == {"employee"}
    assert [m.level_tag for m in response.context["messages"]] == ["error"]


# Import ---------------------------------------------------------------------------


def csv_file(text):
    return SimpleUploadedFile("users.csv", text.encode(), content_type="text/csv")


def test_import_with_invalid_line_lists_errors_and_creates_nobody(admin_client):
    text = f"{HEADER}\nann@example.com,Ann,Lee,,\nbroken,X,Y,,\n"
    response = admin_client.post(reverse("manage:user_import"), {"file": csv_file(text)})
    assert response.status_code == 200
    content = response.content.decode()
    assert "broken" not in User.objects.values_list("email", flat=True)
    assert not User.objects.filter(email="ann@example.com").exists()
    assert response.context["result"].errors[0].line == 3
    assert ">3<" in content


def test_import_valid_file_shows_created_count(admin_client):
    text = f"{HEADER}\nann@example.com,Ann,Lee,,\n"
    response = admin_client.post(reverse("manage:user_import"), {"file": csv_file(text)})
    assert response.status_code == 200
    assert response.context["result"].created == 1
    assert User.objects.filter(email="ann@example.com").exists()


def test_import_without_file_shows_form_error(admin_client):
    response = admin_client.post(reverse("manage:user_import"), {})
    assert response.status_code == 200
    assert response.context["form"].errors["file"]


# Navigation -----------------------------------------------------------------------


def test_administration_link_only_for_user_managers(client, admin_client, make_user):
    link = f'href="{reverse("manage:user_list")}"'
    assert link in admin_client.get(reverse("home")).content.decode()
    employee = make_user("emp@example.com")
    client2 = Client()
    client2.force_login(employee)
    assert link not in client2.get(reverse("home")).content.decode()
