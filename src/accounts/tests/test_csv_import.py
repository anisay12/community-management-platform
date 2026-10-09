import io
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts import services
from accounts.models import User
from accounts.roles import user_roles
from accounts.services import MAX_IMPORT_BYTES, MAX_IMPORT_ROWS, import_users_csv
from audit.models import AuditEvent
from core.errors import DomainError
from organizations.models import OrganizationUnit

pytestmark = pytest.mark.django_db

HEADER = "email,first_name,last_name,unit_code,manager_email"


@pytest.fixture(autouse=True)
def units():
    OrganizationUnit.objects.create(code="ENG", name="Engineering")
    OrganizationUnit.objects.create(code="OPS", name="Operations")


def upload(text: str, *, encoding="utf-8") -> io.BytesIO:
    return io.BytesIO(text.encode(encoding))


def run(actor, text, **kwargs):
    return import_users_csv(actor=actor, file=upload(text, **kwargs))


def imported_count():
    return User.objects.exclude(email="fadmin@example.com").count()


def test_valid_csv_with_manager_defined_later_creates_everyone(
    functional_admin, mailoutbox, django_capture_on_commit_callbacks
):
    text = "\n".join(
        [
            HEADER,
            "ann@example.com,Ann,Lee,ENG,boss@example.com",
            "boss@example.com,Big,Boss,ENG,",
            "cid@example.com,Cid,Moe,,",
        ]
    )
    with django_capture_on_commit_callbacks(execute=True):
        result = run(functional_admin, text)
    assert result.errors == []
    assert result.created == 3
    ann = User.objects.get(email="ann@example.com")
    boss = User.objects.get(email="boss@example.com")
    assert ann.status == User.Status.PENDING
    assert ann.employment.unit.code == "ENG"
    assert ann.employment.manager == boss
    assert boss.employment.manager is None
    assert not hasattr(User.objects.get(email="cid@example.com"), "employment")
    assert user_roles(ann) == {"employee"}
    assert sorted(m.to[0] for m in mailoutbox) == [
        "ann@example.com",
        "boss@example.com",
        "cid@example.com",
    ]
    assert AuditEvent.objects.filter(action="user.imported").count() == 3
    summary = AuditEvent.objects.get(action="users.csv_import")
    assert summary.changes == {"created": 3}
    assert summary.actor == functional_admin


def test_one_invalid_line_creates_nobody(functional_admin, mailoutbox):
    text = "\n".join(
        [
            HEADER,
            "ann@example.com,Ann,Lee,ENG,",
            "not-an-email,Bad,Line,ENG,",
            "bob@example.com,Bob,Ray,ENG,",
        ]
    )
    result = run(functional_admin, text)
    assert result.created == 0
    assert [(e.line, bool(e.message)) for e in result.errors] == [(3, True)]
    assert imported_count() == 0
    assert mailoutbox == []
    assert not AuditEvent.objects.filter(action__in=["user.imported", "users.csv_import"]).exists()


def test_semicolon_delimiter_and_bom_are_accepted(functional_admin):
    text = "﻿" + HEADER.replace(",", ";") + "\nann@example.com;Ann;Lee;OPS;\n"
    result = run(functional_admin, text)
    assert result.errors == []
    assert result.created == 1
    assert User.objects.get(email="ann@example.com").employment.unit.code == "OPS"


def test_existing_manager_in_database_is_used(functional_admin, make_user):
    boss = make_user("boss@example.com")
    result = run(functional_admin, f"{HEADER}\nann@example.com,Ann,Lee,ENG,BOSS@example.com\n")
    assert result.errors == []
    assert User.objects.get(email="ann@example.com").employment.manager == boss


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        ("not-an-email,A,B,ENG,", "email"),
        ("a" * 64 + "@" + ("b" * 60 + ".") * 4 + "com,A,B,ENG,", "email"),
        ("ann@example.com,,B,ENG,", "first name"),
        ("ann@example.com,A,,ENG,", "last name"),
        ("ann@example.com," + "A" * 151 + ",B,ENG,", "150"),
        ("ann@example.com,A,B,NOPE,", "NOPE"),
        ("ann@example.com,A,B,ENG,ghost@example.com", "ghost@example.com"),
        ("ann@example.com,A,B,ENG,ann@example.com", "own manager"),
        ("ann@example.com,A,B,,boss@example.com", "unit"),
        ("ann@example.com,A,B,ENG", "5 columns"),
        ("taken@example.com,A,B,ENG,", "already"),
    ],
)
def test_line_errors(functional_admin, make_user, line, fragment):
    make_user("taken@example.com")
    make_user("boss@example.com")
    result = run(functional_admin, f"{HEADER}\n{line}\n")
    assert result.created == 0
    assert len(result.errors) == 1
    assert result.errors[0].line == 2
    assert fragment in result.errors[0].message


def test_duplicate_in_file_is_reported_on_second_occurrence(functional_admin):
    text = f"{HEADER}\nann@example.com,A,B,ENG,\nANN@example.com,A,B,ENG,\n"
    result = run(functional_admin, text)
    assert [e.line for e in result.errors] == [3]
    assert "line 2" in result.errors[0].message


def test_all_errors_are_listed(functional_admin):
    text = f"{HEADER}\nbad,A,B,ENG,\nok@example.com,A,B,ENG,\nworse,A,B,ENG,\n"
    assert [e.line for e in run(functional_admin, text).errors] == [2, 4]


@pytest.mark.parametrize(
    "header",
    [
        "email,first_name,last_name,unit_code",
        "email,last_name,first_name,unit_code,manager_email",
        "email;first_name,last_name,unit_code,manager_email",
        "",
    ],
)
def test_wrong_header_is_rejected(functional_admin, header):
    result = run(functional_admin, f"{header}\nann@example.com,A,B,ENG,\n")
    assert result.created == 0
    assert result.errors[0].line == 1
    assert imported_count() == 0


def test_header_only_file_is_rejected(functional_admin):
    result = run(functional_admin, HEADER + "\n")
    assert result.created == 0
    assert result.errors


def test_non_utf8_file_is_rejected(functional_admin):
    result = run(functional_admin, f"{HEADER}\nann@example.com,Zoë,B,ENG,\n", encoding="latin-1")
    assert result.created == 0
    assert "UTF-8" in result.errors[0].message


def test_too_many_rows_is_rejected(functional_admin):
    rows = [f"u{i}@example.com,A,B,," for i in range(MAX_IMPORT_ROWS + 1)]
    result = run(functional_admin, "\n".join([HEADER, *rows]))
    assert result.created == 0
    assert "5000" in result.errors[0].message
    assert imported_count() == 0


def test_too_large_file_is_rejected(functional_admin):
    text = HEADER + "\n" + "x" * MAX_IMPORT_BYTES
    result = run(functional_admin, text)
    assert result.created == 0
    assert result.errors[0].line == 0
    assert imported_count() == 0


def test_blank_lines_are_ignored(functional_admin):
    result = run(functional_admin, f"{HEADER}\n\nann@example.com,A,B,ENG,\n\n")
    assert result.errors == []
    assert result.created == 1


def test_nul_byte_is_rejected(functional_admin):
    result = run(functional_admin, f"{HEADER}\nann@example.com,A\x00,B,ENG,\n")
    assert result.created == 0
    assert result.errors[0].line == 0
    assert imported_count() == 0


def test_oversized_field_is_rejected(functional_admin):
    result = run(functional_admin, f"{HEADER}\nann@example.com,{'A' * 200_000},B,ENG,\n")
    assert result.created == 0
    assert result.errors
    assert imported_count() == 0


def email_taken_on_second_row():
    """Let the first row through, then fail as if its email had been taken concurrently."""
    real = services.create_user
    calls = []

    def fake(**kwargs):
        calls.append(kwargs["email"])
        if len(calls) == 2:
            raise DomainError("email_taken", "An account already exists for b@example.com.")
        return real(**kwargs)

    return mock.patch.object(services, "create_user", side_effect=fake)


def test_concurrent_email_taken_during_creation_rolls_everything_back(
    functional_admin, mailoutbox, django_capture_on_commit_callbacks
):
    text = f"{HEADER}\na@example.com,A,B,ENG,\nb@example.com,A,B,ENG,\n"
    with email_taken_on_second_row(), django_capture_on_commit_callbacks(execute=True):
        result = run(functional_admin, text)
    assert result.created == 0
    assert [(e.line, e.message) for e in result.errors] == [
        (3, "An account already exists for b@example.com.")
    ]
    assert imported_count() == 0
    assert not AuditEvent.objects.filter(action__in=["user.imported", "users.csv_import"]).exists()
    assert mailoutbox == []


def test_concurrent_email_taken_is_shown_on_the_result_page(
    client, functional_admin, verified_login
):
    verified_login(client, functional_admin)
    text = f"{HEADER}\na@example.com,A,B,ENG,\nb@example.com,A,B,ENG,\n"
    upload_file = SimpleUploadedFile("users.csv", text.encode(), content_type="text/csv")
    with email_taken_on_second_row():
        response = client.post(reverse("manage:user_import"), {"file": upload_file}, follow=True)
    assert response.status_code == 200
    assert response.context["errors"] == [
        {"line": 3, "message": "An account already exists for b@example.com."}
    ]
    assert imported_count() == 0
