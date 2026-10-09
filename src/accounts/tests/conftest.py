import pytest

from accounts.models import User

PASSWORD = "correct-horse-battery-staple"


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
