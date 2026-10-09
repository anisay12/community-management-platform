import pytest
from django.db import IntegrityError, transaction

from accounts.models import User
from organizations.models import Employment, OrganizationUnit

pytestmark = pytest.mark.django_db


def make_user(email="a@example.com"):
    return User.objects.create_user(email, first_name="A", last_name="B")


def test_unit_str_and_parent():
    root = OrganizationUnit.objects.create(name="Engineering", code="ENG")
    child = OrganizationUnit.objects.create(name="Data", code="ENG-DATA", parent=root)
    assert str(child) == "ENG-DATA — Data"
    assert list(root.children.all()) == [child]


def test_employment_manager_cannot_be_the_user():
    unit = OrganizationUnit.objects.create(name="Engineering", code="ENG")
    user = make_user()
    with pytest.raises(IntegrityError), transaction.atomic():
        Employment.objects.create(user=user, unit=unit, manager=user)


def test_employment_links_manager_and_reports():
    unit = OrganizationUnit.objects.create(name="Engineering", code="ENG")
    boss = make_user("boss@example.com")
    emp = make_user("emp@example.com")
    Employment.objects.create(user=emp, unit=unit, manager=boss)
    assert boss.reports.count() == 1
