import uuid
from datetime import UTC, datetime

import pytest
from django.contrib.auth.models import AnonymousUser
from django.urls import NoReverseMatch, reverse

from accounts.models import User, UserProfile
from accounts.policies import can_view_profile, can_view_profile_details, shares_community
from audit.models import AuditEvent
from organizations.models import Employment, OrganizationUnit
from taxonomy.models import Tag

pytestmark = pytest.mark.django_db

Visibility = UserProfile.Visibility


@pytest.fixture
def owner(make_user):
    user = make_user("bob@example.com", first_name="Bob", last_name="Ray")
    profile = user.profile
    profile.job_title = "Data engineer"
    profile.bio = "Loves graph databases."
    profile.save()
    profile.interests.add(Tag.objects.create(name="Kayak", slug="kayak"))
    return user


def set_visibility(user, visibility):
    user.profile.profile_visibility = visibility
    user.profile.save()


def detail_url(user):
    return reverse("accounts:profile_detail", args=[user.public_id])


# Routes ------------------------------------------------------------------------


def test_routes():
    public_id = uuid.uuid4()
    assert reverse("accounts:profile_me") == "/me/"
    assert reverse("accounts:profile_edit") == "/me/edit/"
    assert reverse("accounts:preferences") == "/me/preferences/"
    assert reverse("accounts:profile_detail", args=[public_id]) == f"/people/{public_id}/"
    assert reverse("accounts:login") == "/accounts/login/"


def test_profile_pages_require_login(client, owner):
    for url in (
        reverse("accounts:profile_me"),
        reverse("accounts:profile_edit"),
        reverse("accounts:preferences"),
        detail_url(owner),
    ):
        response = client.get(url)
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("accounts:login"))


def test_profile_me_redirects_to_own_profile(client, active_user):
    client.force_login(active_user)
    response = client.get(reverse("accounts:profile_me"))
    assert response.status_code == 302
    assert response["Location"] == detail_url(active_user)


# Policies ----------------------------------------------------------------------


def test_can_view_profile_rules(make_user, functional_admin, owner):
    viewer = make_user("eve@example.com")
    assert can_view_profile(viewer, owner)
    assert can_view_profile(owner, owner)
    assert not can_view_profile(AnonymousUser(), owner)
    for status in (User.Status.PENDING, User.Status.SUSPENDED, User.Status.DEACTIVATED):
        owner.status = status
        owner.save()
        assert not can_view_profile(viewer, owner)
        assert can_view_profile(functional_admin, owner)
        # The viewer must be active: an inactive account sees no profile, not even its own.
        assert not can_view_profile(owner, owner)


def test_inactive_viewer_cannot_view_profiles(make_user, owner):
    viewer = make_user("eve@example.com", status=User.Status.SUSPENDED)
    assert not can_view_profile(viewer, owner)
    assert not can_view_profile_details(viewer, owner)


@pytest.mark.parametrize(
    ("visibility", "expected"),
    [(Visibility.COMPANY, True), (Visibility.COMMUNITIES, False), (Visibility.PRIVATE, False)],
)
def test_can_view_profile_details_by_visibility(make_user, owner, visibility, expected):
    viewer = make_user("eve@example.com")
    set_visibility(owner, visibility)
    assert can_view_profile_details(viewer, owner) is expected
    assert can_view_profile_details(owner, owner)


def test_admin_always_sees_details(functional_admin, owner):
    set_visibility(owner, Visibility.PRIVATE)
    assert can_view_profile_details(functional_admin, owner)


def test_shares_community_is_false_until_communities_exist(make_user, owner):
    assert shares_community(make_user("eve@example.com"), owner) is False


# Profile page ------------------------------------------------------------------


def test_viewer_sees_name_job_title_unit_initials_and_company_bio(client, make_user, owner):
    unit = OrganizationUnit.objects.create(name="Data Lab", code="DL")
    Employment.objects.create(user=owner, unit=unit)
    client.force_login(make_user("eve@example.com"))
    response = client.get(detail_url(owner))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Bob Ray" in content
    assert "Data engineer" in content
    assert "Data Lab" in content
    assert ">BR<" in content
    assert "Loves graph databases." in content
    assert "Kayak" in content
    assert "Profile photos" not in content
    assert reverse("accounts:profile_edit") not in content


@pytest.mark.parametrize("visibility", [Visibility.PRIVATE, Visibility.COMMUNITIES])
def test_bio_and_interests_hidden_unless_visible(client, make_user, owner, visibility):
    set_visibility(owner, visibility)
    client.force_login(make_user("eve@example.com"))
    response = client.get(detail_url(owner))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Bob Ray" in content
    assert "Data engineer" in content
    assert "Loves graph databases." not in content
    assert "Kayak" not in content


def test_owner_sees_own_private_details_and_edit_link(client, owner):
    set_visibility(owner, Visibility.PRIVATE)
    client.force_login(owner)
    content = client.get(detail_url(owner)).content.decode()
    assert "Loves graph databases." in content
    assert reverse("accounts:profile_edit") in content


def test_deactivated_profile_404_for_employee_200_for_admin(
    client, make_user, functional_admin, verified_login, owner
):
    owner.status = User.Status.DEACTIVATED
    owner.save()
    client.force_login(make_user("eve@example.com"))
    assert client.get(detail_url(owner)).status_code == 404
    verified_login(client, functional_admin)
    assert client.get(detail_url(owner)).status_code == 200


def test_unknown_public_id_is_404(client, active_user):
    client.force_login(active_user)
    response = client.get(reverse("accounts:profile_detail", args=[uuid.uuid4()]))
    assert response.status_code == 404


def test_profile_detail_query_count(client, make_user, owner, django_assert_max_num_queries):
    client.force_login(make_user("eve@example.com"))
    client.get(detail_url(owner))
    with django_assert_max_num_queries(8):
        assert client.get(detail_url(owner)).status_code == 200


# Edit --------------------------------------------------------------------------


def edit_data(**overrides):
    data = {
        "job_title": "Architect",
        "bio": "New bio",
        "interests": "Python, kayak ,  python,Open  Source",
        "profile_visibility": Visibility.COMMUNITIES,
        "is_discoverable": "",
    }
    data.update(overrides)
    return data


def test_edit_own_profile(client, owner):
    client.force_login(owner)
    assert client.get(reverse("accounts:profile_edit")).status_code == 200
    response = client.post(reverse("accounts:profile_edit"), edit_data())
    assert response.status_code == 302
    assert response["Location"] == detail_url(owner)
    profile = UserProfile.objects.get(user=owner)
    assert profile.job_title == "Architect"
    assert profile.bio == "New bio"
    assert profile.profile_visibility == Visibility.COMMUNITIES
    assert profile.is_discoverable is False
    # Existing tags are reused case-insensitively; duplicates and spaces are normalised.
    assert sorted(profile.interests.values_list("name", flat=True)) == [
        "Kayak",
        "Open Source",
        "Python",
    ]
    assert Tag.objects.count() == 3
    event = AuditEvent.objects.get(action="profile.updated")
    assert event.target_id == str(owner.public_id)
    assert event.changes == {
        "fields": ["bio", "interests", "is_discoverable", "job_title", "profile_visibility"]
    }


def test_edit_form_shows_current_interests(client, owner):
    client.force_login(owner)
    content = client.get(reverse("accounts:profile_edit")).content.decode()
    assert 'value="Kayak"' in content


def test_bio_too_long_shows_inline_error(client, owner):
    client.force_login(owner)
    response = client.post(reverse("accounts:profile_edit"), edit_data(bio="x" * 2001))
    assert response.status_code == 200
    assert "bio" in response.context["form"].errors
    assert 'class="field-error"' in response.content.decode()
    assert UserProfile.objects.get(user=owner).bio == "Loves graph databases."


def test_too_many_interests_rejected(client, owner):
    client.force_login(owner)
    names = ", ".join(f"tag{i}" for i in range(11))
    response = client.post(reverse("accounts:profile_edit"), edit_data(interests=names))
    assert response.status_code == 200
    assert "interests" in response.context["form"].errors
    assert not Tag.objects.filter(name="tag0").exists()


def test_too_long_interest_rejected(client, owner):
    client.force_login(owner)
    response = client.post(reverse("accounts:profile_edit"), edit_data(interests="x" * 65))
    assert response.status_code == 200
    assert "interests" in response.context["form"].errors


def test_tag_with_unsluggable_name_gets_unique_slug(client, owner):
    Tag.objects.create(name="C", slug="c")
    client.force_login(owner)
    response = client.post(reverse("accounts:profile_edit"), edit_data(interests="C++, 日本, !!"))
    assert response.status_code == 302
    slugs = set(Tag.objects.values_list("slug", flat=True))
    assert len(slugs) == Tag.objects.count()
    assert all(slugs)


def test_no_route_edits_another_users_profile(client, make_user, owner):
    client.force_login(make_user("eve@example.com"))
    with pytest.raises(NoReverseMatch):
        reverse("accounts:profile_edit", args=[owner.public_id])
    assert client.post(f"{detail_url(owner)}edit/", edit_data()).status_code == 404
    assert client.post(detail_url(owner), edit_data()).status_code == 405
    # The edit page only ever changes the logged-in user's own profile.
    client.post(reverse("accounts:profile_edit"), edit_data(job_title="Hacked"))
    assert UserProfile.objects.get(user=owner).job_title == "Data engineer"


# Preferences -------------------------------------------------------------------


def test_preferences_save_language_and_timezone(client, active_user):
    client.force_login(active_user)
    assert client.get(reverse("accounts:preferences")).status_code == 200
    response = client.post(
        reverse("accounts:preferences"), {"language": "fr", "timezone": "Asia/Tokyo"}
    )
    assert response.status_code == 302
    profile = UserProfile.objects.get(user=active_user)
    assert profile.language == "fr"
    assert profile.timezone == "Asia/Tokyo"
    assert response.cookies["django_language"].value == "fr"


def test_preferences_reject_unknown_timezone(client, active_user):
    client.force_login(active_user)
    response = client.post(
        reverse("accounts:preferences"), {"language": "", "timezone": "Mars/Olympus"}
    )
    assert response.status_code == 200
    assert "timezone" in response.context["form"].errors


def test_timezone_preference_changes_rendered_datetimes(client, active_user):
    # A future last_seen_at is never refreshed by the activity tracker.
    User.objects.filter(pk=active_user.pk).update(
        last_seen_at=datetime(2030, 1, 15, 12, 0, tzinfo=UTC)
    )
    client.force_login(active_user)
    client.post(reverse("accounts:preferences"), {"language": "en", "timezone": "Asia/Tokyo"})
    content = client.get(detail_url(active_user)).content.decode()
    assert "Jan. 15, 2030, 9 p.m." in content  # 12:00 UTC is 21:00 in Tokyo
    client.post(reverse("accounts:preferences"), {"language": "en", "timezone": "UTC"})
    content = client.get(detail_url(active_user)).content.decode()
    assert "Jan. 15, 2030, noon" in content


def test_user_menu_links(client, active_user):
    client.force_login(active_user)
    content = client.get(reverse("home")).content.decode()
    assert f'href="{reverse("accounts:profile_me")}"' in content
    assert f'href="{reverse("accounts:preferences")}"' in content
    assert f'action="{reverse("accounts:logout")}"' in content
