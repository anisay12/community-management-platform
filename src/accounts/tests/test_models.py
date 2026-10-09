import uuid

import pytest
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import ExternalIdentity, User, UserProfile
from accounts.roles import PRIVILEGED_ROLES, Role, has_role, is_manager, user_roles
from organizations.models import Employment, OrganizationUnit

pytestmark = pytest.mark.django_db


def make_user(email="alice@example.com", **extra):
    return User.objects.create_user(email, first_name="Alice", last_name="Doe", **extra)


def test_emails_differing_by_case_are_rejected():
    make_user("alice@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create(email="Alice@EXAMPLE.com", first_name="A", last_name="B")


def test_get_by_natural_key_is_case_insensitive():
    user = make_user("alice@example.com")
    assert User.objects.get_by_natural_key("ALICE@Example.com") == user


def test_save_lowercases_email():
    user = make_user("Alice@Example.COM")
    assert user.email == "alice@example.com"


def test_create_user_defaults():
    user = make_user()
    assert user.status == User.Status.PENDING
    assert not user.has_usable_password()
    assert isinstance(user.public_id, uuid.UUID)
    user.refresh_from_db()
    assert user.is_active is False


def test_create_user_with_password():
    user = make_user(password="s3cret-pass")
    assert user.check_password("s3cret-pass")


def test_create_superuser():
    admin = User.objects.create_superuser("root@example.com", "pw", first_name="R", last_name="T")
    admin.refresh_from_db()
    assert admin.status == User.Status.ACTIVE
    assert admin.is_staff and admin.is_superuser and admin.is_active
    assert admin.activated_at is not None


def test_is_active_only_for_active_status_and_filterable():
    active = make_user("a@example.com", status=User.Status.ACTIVE)
    for i, status in enumerate(["pending", "suspended", "deactivated"]):
        make_user(f"u{i}@example.com", status=status)
    assert list(User.objects.filter(is_active=True)) == [active]
    active.refresh_from_db()
    assert active.is_active is True


def test_full_name_and_short_name():
    user = make_user()
    assert user.get_full_name() == "Alice Doe"
    assert user.get_short_name() == "Alice"


def test_full_name_for_anonymized_user():
    user = make_user()
    user.anonymized_at = timezone.now()
    assert user.get_full_name() == "Former employee"


def test_profile_auto_created_with_defaults():
    user = make_user()
    profile = user.profile
    assert profile.timezone == "Europe/Paris"
    assert profile.profile_visibility == UserProfile.Visibility.COMPANY
    assert profile.is_discoverable is True
    assert profile.language == ""


def test_profile_not_duplicated_on_resave():
    user = make_user()
    user.save()
    assert UserProfile.objects.filter(user=user).count() == 1


def test_profile_bio_too_long_fails_validation():
    profile = make_user().profile
    profile.bio = "x" * 2001
    with pytest.raises(ValidationError) as exc:
        profile.full_clean()
    assert "bio" in exc.value.message_dict


def test_profile_invalid_timezone_fails_validation():
    profile = make_user().profile
    profile.timezone = "Mars/Olympus"
    with pytest.raises(ValidationError) as exc:
        profile.full_clean()
    assert "timezone" in exc.value.message_dict


def test_five_role_groups_exist():
    assert set(Group.objects.values_list("name", flat=True)) >= {
        "employee",
        "community_creator",
        "functional_admin",
        "technical_admin",
        "auditor",
    }


def test_role_helpers():
    user = make_user()
    assert user_roles(user) == set()
    user.groups.add(Group.objects.get(name="auditor"))
    user = User.objects.get(pk=user.pk)
    assert user_roles(user) == {"auditor"}
    assert has_role(user, Role.AUDITOR)
    assert not has_role(user, Role.TECHNICAL_ADMIN)
    assert not has_role(AnonymousUser(), Role.AUDITOR)
    assert user_roles(AnonymousUser()) == set()
    assert {Role.FUNCTIONAL_ADMIN, Role.TECHNICAL_ADMIN} == PRIVILEGED_ROLES
    assert str(Role.COMMUNITY_CREATOR.label) == "Community creator"


def test_is_manager():
    unit = OrganizationUnit.objects.create(name="Eng", code="ENG")
    boss = make_user("boss@example.com")
    emp = make_user("emp@example.com")
    assert not is_manager(boss)
    Employment.objects.create(user=emp, unit=unit, manager=boss)
    assert is_manager(boss)


def test_external_identity_unique_per_provider_subject():
    a, b = make_user("a@example.com"), make_user("b@example.com")
    ExternalIdentity.objects.create(user=a, provider="oidc", subject="s1")
    with pytest.raises(IntegrityError), transaction.atomic():
        ExternalIdentity.objects.create(user=b, provider="oidc", subject="s1")


def test_external_identity_provider_and_subject_are_immutable():
    identity = ExternalIdentity.objects.create(user=make_user(), provider="oidc", subject="s1")
    identity.subject = "s2"
    with pytest.raises(ValueError, match="immutable"):
        identity.save()
    identity.refresh_from_db()
    identity.last_login_at = timezone.now()
    identity.save()


def test_is_active_reflects_status_after_save_without_refresh():
    user = make_user(status=User.Status.ACTIVE)
    user.refresh_from_db()
    assert user.is_active is True
    user.status = User.Status.SUSPENDED
    user.save()
    assert user.is_active is False

    pending = make_user("bob@example.com")
    assert pending.is_active is False
    pending.status = User.Status.ACTIVE
    pending.save()
    assert pending.is_active is True


def test_email_unique_constraint_is_declared():
    names = {c.name for c in User._meta.constraints}
    assert "accounts_user_email_ci_unique" in names


def test_email_unique_constraint_enforced_in_database_bypassing_save():
    make_user("bob@x.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.bulk_create([User(email="Bob@x.com", first_name="B", last_name="B")])


def test_profile_not_created_for_raw_saves():
    from accounts.signals import create_profile

    user = make_user()
    UserProfile.objects.filter(user=user).delete()
    create_profile(sender=User, instance=user, created=True, raw=True)
    assert not UserProfile.objects.filter(user=user).exists()
    create_profile(sender=User, instance=user, created=True, raw=False)
    assert UserProfile.objects.filter(user=user).exists()
