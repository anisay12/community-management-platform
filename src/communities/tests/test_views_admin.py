"""HTTP tests of the functional administration: categories and creation requests."""

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from accounts.roles import Role
from audit.models import AuditEvent
from communities.models import (
    Community,
    CommunityCategory,
    CommunityCreationRequest,
    CommunityMembership,
)
from core.tests.helpers import assert_single_h1
from notifications.models import Notification

pytestmark = pytest.mark.django_db

CATEGORY_LIST = "/manage/categories/"
CATEGORY_CREATE = "/manage/categories/new/"
REQUEST_LIST = "/manage/creation-requests/"


@pytest.fixture
def admin_client(client, functional_admin, verified_login):
    return verified_login(client, functional_admin)


@pytest.fixture
def requester(make_user):
    return make_user("req@example.com", first_name="Remy", last_name="Requester")


@pytest.fixture
def creation_request(requester, category):
    return CommunityCreationRequest.objects.create(
        requester=requester, name="Rust guild", category=category, justification="We love Rust"
    )


def _category_data(**overrides):
    data = {"name": "Quantum", "slug": "", "description": "", "icon": "cpu", "order": 5}
    data["is_active"] = "on"
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


# --- Access ---------------------------------------------------------------------------------


@pytest.mark.parametrize("role", [None, Role.AUDITOR, Role.COMMUNITY_CREATOR])
def test_non_admins_get_404(client, make_user, verified_login, category, creation_request, role):
    user = make_user("other@example.com")
    if role:
        user.groups.add(Group.objects.get(name=role))
    if role == Role.AUDITOR:  # a privileged role: MFA is required before any page
        verified_login(client, user)
    else:
        client.force_login(user)
    urls = [
        CATEGORY_LIST,
        CATEGORY_CREATE,
        reverse("manage:category_edit", args=[category.slug]),
        REQUEST_LIST,
    ]
    for url in urls:
        assert client.get(url).status_code == 404
    decide = reverse("manage:creation_request_decide", args=[creation_request.public_id])
    assert client.post(decide, {"decision": "approve"}).status_code == 404
    creation_request.refresh_from_db()
    assert creation_request.status == CommunityCreationRequest.Status.PENDING


def test_anonymous_cannot_see_pages(client):
    assert client.get(CATEGORY_LIST).status_code in (302, 404)


def test_tabs_show_admin_entries(admin_client):
    content = admin_client.get("/manage/users/").content.decode()
    assert CATEGORY_LIST in content
    assert REQUEST_LIST in content


# --- Categories -----------------------------------------------------------------------------


def test_category_list_shows_count_and_flag(admin_client, category, make_community):
    make_community("One")
    make_community("Two")
    response = admin_client.get(CATEGORY_LIST)
    assert response.status_code == 200
    assert_single_h1(response)
    row = next(c for c in response.context["categories"] if c.pk == category.pk)
    assert row.community_count == 2
    assert category.name in response.content.decode()


def test_create_category_with_auto_slug(admin_client):
    response = admin_client.post(CATEGORY_CREATE, _category_data())
    assert response.status_code == 302
    created = CommunityCategory.objects.get(name="Quantum")
    assert created.slug == "quantum"
    assert created.icon == "cpu"
    assert created.order == 5
    assert created.is_active
    assert AuditEvent.objects.filter(action="category.created").exists()


def test_create_category_with_custom_slug_inactive(admin_client):
    data = _category_data(slug="q-lab", is_active=None)
    assert admin_client.post(CATEGORY_CREATE, data).status_code == 302
    created = CommunityCategory.objects.get(name="Quantum")
    assert created.slug == "q-lab"
    assert not created.is_active


def test_create_category_inactive_is_audited_once(admin_client):
    assert admin_client.post(CATEGORY_CREATE, _category_data(is_active=None)).status_code == 302
    actions = list(AuditEvent.objects.values_list("action", flat=True))
    assert actions.count("category.created") == 1
    assert "category.deactivated" not in actions


def test_edit_seeded_categories_without_changes_keeps_icon(admin_client):
    seeded = list(CommunityCategory.objects.all())
    assert len(seeded) >= 12
    for category in seeded:
        url = reverse("manage:category_edit", args=[category.slug])
        form = admin_client.get(url).context["form"]
        data = {key: value for key, value in form.initial.items() if value not in (None, False)}
        response = admin_client.post(url, data)
        assert response.status_code == 302, (category.icon, response.context["form"].errors)
        refreshed = CommunityCategory.objects.get(pk=category.pk)
        assert refreshed.icon == category.icon


def test_edit_category_keeps_custom_icon(admin_client, category):
    CommunityCategory.objects.filter(pk=category.pk).update(icon="atom")
    url = reverse("manage:category_edit", args=[category.slug])
    data = _category_data(name=category.name, slug=category.slug, icon="atom")
    assert admin_client.post(url, data).status_code == 302
    category.refresh_from_db()
    assert category.icon == "atom"
    assert admin_client.post(CATEGORY_CREATE, _category_data(icon="atom")).status_code == 200


def test_edit_category_slug_change_is_audited(admin_client, category):
    url = reverse("manage:category_edit", args=[category.slug])
    data = _category_data(name=category.name, slug="new-slug")
    assert admin_client.post(url, data).status_code == 302
    event = AuditEvent.objects.get(action="category.updated")
    assert event.changes["slug"] == {"before": category.slug, "after": "new-slug"}


def test_create_category_rejects_duplicates(admin_client, category):
    before = CommunityCategory.objects.count()
    response = admin_client.post(CATEGORY_CREATE, _category_data(name="test CATEGORY"))
    assert response.status_code == 200
    assert response.context["form"].errors["name"]
    response = admin_client.post(CATEGORY_CREATE, _category_data(slug="test-category"))
    assert response.status_code == 200
    assert response.context["form"].errors["slug"]
    assert CommunityCategory.objects.count() == before


def test_create_category_rejects_unknown_icon(admin_client):
    response = admin_client.post(CATEGORY_CREATE, _category_data(icon='evil"><script>'))
    assert response.status_code == 200
    assert response.context["form"].errors["icon"]


def test_edit_category(admin_client, category):
    url = reverse("manage:category_edit", args=[category.slug])
    assert admin_client.get(url).status_code == 200
    response = admin_client.post(url, _category_data(name="Renamed", slug="renamed", order=2))
    assert response.status_code == 302
    category.refresh_from_db()
    assert (category.name, category.slug, category.order) == ("Renamed", "renamed", 2)
    assert AuditEvent.objects.filter(action="category.updated").exists()


def test_edit_category_duplicate_name(admin_client, category):
    CommunityCategory.objects.create(name="Other", slug="other")
    url = reverse("manage:category_edit", args=[category.slug])
    response = admin_client.post(url, _category_data(name="other", slug="test-category"))
    assert response.status_code == 200
    assert response.context["form"].errors["name"]


def test_deactivate_category_in_use_keeps_communities(admin_client, category, make_community):
    community = make_community("Kept")
    url = reverse("manage:category_edit", args=[category.slug])
    data = _category_data(name=category.name, slug=category.slug, is_active=None)
    assert admin_client.post(url, data).status_code == 302
    category.refresh_from_db()
    assert not category.is_active
    assert Community.objects.get(pk=community.pk).category == category
    assert AuditEvent.objects.filter(action="category.deactivated").exists()


def test_edit_unknown_category_404(admin_client):
    assert admin_client.get(reverse("manage:category_edit", args=["nope"])).status_code == 404


# --- Creation requests ----------------------------------------------------------------------


def test_request_list_pending_first(admin_client, creation_request, requester, category):
    done = CommunityCreationRequest.objects.create(
        requester=requester,
        name="Old idea",
        category=category,
        justification="x",
        status=CommunityCreationRequest.Status.REJECTED,
    )
    response = admin_client.get(REQUEST_LIST)
    assert response.status_code == 200
    assert_single_h1(response)
    rows = list(response.context["page_obj"])
    assert rows == [creation_request, done]
    assert "req@example.com" not in response.content.decode()


def test_request_decision_buttons_described_by_heading(admin_client, creation_request):
    page = admin_client.get(REQUEST_LIST).content.decode()
    heading_id = f"creation-request-{creation_request.public_id}"
    assert f'id="{heading_id}"' in page
    assert page.count(f'aria-describedby="{heading_id}"') == 2


def test_admin_tabs_follow_category_and_admin_guards(admin_client):
    page = admin_client.get(CATEGORY_LIST).content.decode()
    assert CATEGORY_LIST in page and REQUEST_LIST in page


def test_approve_request(
    admin_client, django_capture_on_commit_callbacks, creation_request, requester, functional_admin
):
    url = reverse("manage:creation_request_decide", args=[creation_request.public_id])
    with django_capture_on_commit_callbacks(execute=True):
        response = admin_client.post(url, {"decision": "approve", "note": "Welcome"})
    assert response.status_code == 302
    creation_request.refresh_from_db()
    assert creation_request.status == CommunityCreationRequest.Status.APPROVED
    assert creation_request.decision_note == "Welcome"
    community = creation_request.community
    assert CommunityMembership.objects.get(community=community, user=requester).role == "owner"
    assert AuditEvent.objects.filter(action="community.creation_approved").exists()
    assert Notification.objects.filter(
        recipient=requester, category="community.creation_decided"
    ).exists()


def test_reject_request(
    admin_client, django_capture_on_commit_callbacks, creation_request, requester
):
    url = reverse("manage:creation_request_decide", args=[creation_request.public_id])
    with django_capture_on_commit_callbacks(execute=True):
        response = admin_client.post(url, {"decision": "reject", "note": "Duplicate"})
    assert response.status_code == 302
    creation_request.refresh_from_db()
    assert creation_request.status == CommunityCreationRequest.Status.REJECTED
    assert creation_request.decision_note == "Duplicate"
    assert AuditEvent.objects.filter(action="community.creation_rejected").exists()
    assert Notification.objects.filter(recipient=requester).exists()


def test_decide_twice_shows_message_not_500(admin_client, creation_request):
    url = reverse("manage:creation_request_decide", args=[creation_request.public_id])
    admin_client.post(url, {"decision": "reject"})
    response = admin_client.post(url, {"decision": "approve"}, follow=True)
    assert response.status_code == 200
    assert list(response.context["messages"])
    creation_request.refresh_from_db()
    assert creation_request.status == CommunityCreationRequest.Status.REJECTED


def test_decide_bad_decision_400(admin_client, creation_request):
    url = reverse("manage:creation_request_decide", args=[creation_request.public_id])
    assert admin_client.post(url, {"decision": "maybe"}).status_code == 400


def test_decide_get_not_allowed(admin_client, creation_request):
    url = reverse("manage:creation_request_decide", args=[creation_request.public_id])
    assert admin_client.get(url).status_code == 405


# --- French ---------------------------------------------------------------------------------


def test_pages_render_in_french(admin_client, functional_admin, creation_request):
    functional_admin.language = "fr"
    functional_admin.save()
    content = admin_client.get(CATEGORY_LIST, HTTP_ACCEPT_LANGUAGE="fr").content.decode()
    assert "Catégories" in content
    content = admin_client.get(REQUEST_LIST, HTTP_ACCEPT_LANGUAGE="fr").content.decode()
    assert "Demandes de création" in content
    content = admin_client.get(CATEGORY_CREATE, HTTP_ACCEPT_LANGUAGE="fr").content.decode()
    assert "Nouvelle catégorie" in content
