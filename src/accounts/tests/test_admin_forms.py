import pytest

from accounts.admin_forms import UserAddForm
from accounts.models import User

pytestmark = pytest.mark.django_db


def test_add_form_creates_pending_user_with_unusable_password():
    form = UserAddForm(
        data={"email": "New.User@Example.com", "first_name": "New", "last_name": "User"}
    )
    assert form.is_valid(), form.errors
    user = form.save()
    user.refresh_from_db()
    assert user.email == "new.user@example.com"
    assert user.status == User.Status.PENDING
    assert not user.has_usable_password()


def test_add_form_rejects_duplicate_email_in_other_case():
    User.objects.create_user("bob@example.com", first_name="B", last_name="B")
    form = UserAddForm(data={"email": "BOB@example.com", "first_name": "B", "last_name": "B"})
    assert not form.is_valid()
    assert "email" in form.errors
