"""Acceptance matrix of the framing (section 4.4), at HTTP level, for the rows L3 delivers.

Columns are viewers: a non-member employee, each community role, a manager (an employee with
direct reports who is not a member), the functional administrator (with and without an
"access as administrator" grant), the technical administrator and the auditor.
"""

import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse

from accounts.roles import Role
from communities.models import AdminAccessGrant, Community, CommunityMembership
from communities.roles import CommunityRole
from organizations.models import Employment, OrganizationUnit

pytestmark = pytest.mark.django_db

OPEN, REQUEST, INVITE = Community.AccessMode
ROLES = [role.value for role in CommunityRole]  # member … owner
OUTSIDERS = ["non_member", "manager", "functional_admin", "technical_admin", "auditor"]
VIEWERS = [*ROLES, *OUTSIDERS, "functional_admin_grant"]
PRIVILEGED = {
    "functional_admin": Role.FUNCTIONAL_ADMIN,
    "functional_admin_grant": Role.FUNCTIONAL_ADMIN,
    "technical_admin": Role.TECHNICAL_ADMIN,
    "auditor": Role.AUDITOR,
}


def _allowed(*viewers):
    return {viewer: viewer in viewers for viewer in VIEWERS}


@pytest.fixture
def matrix(make_user, make_community, add_member, verified_login):
    """Three communities (open, on request, unlisted invite) and a signed-in client per viewer."""
    communities = {
        "open": make_community("Open guild", access_mode=OPEN),
        "private": make_community("Private guild", access_mode=REQUEST),
        "secret": make_community("Secret guild", access_mode=INVITE),
    }
    users, clients = {}, {}
    for viewer in VIEWERS:
        user = make_user(f"{viewer}@example.com", first_name=viewer.title(), last_name="Viewer")
        if viewer in PRIVILEGED:
            user.groups.add(Group.objects.get(name=PRIVILEGED[viewer]))
        if viewer in ROLES:
            for community in communities.values():
                add_member(community, user, role=viewer)
        client = Client()
        if viewer in PRIVILEGED:
            verified_login(client, user)
        else:
            client.force_login(user)
        users[viewer], clients[viewer] = user, client
    unit = OrganizationUnit.objects.create(name="Data", code="DATA")
    Employment.objects.create(user=users["non_member"], unit=unit, manager=users["manager"])
    for community in communities.values():
        AdminAccessGrant.objects.create(
            community=community, user=users["functional_admin_grant"], reason="Moderation case"
        )
    return {"communities": communities, "users": users, "clients": clients}


def _statuses(matrix, url):
    return {viewer: matrix["clients"][viewer].get(url).status_code for viewer in VIEWERS}


def _expect(statuses, allowed, ok=200, refused=403):
    expected = {viewer: ok if is_allowed else refused for viewer, is_allowed in allowed.items()}
    assert statuses == expected


# --- View an open community and its content ---------------------------------------------------


def test_open_community_metadata_is_visible_to_every_viewer(matrix):
    slug = matrix["communities"]["open"].slug
    statuses = _statuses(matrix, reverse("communities:detail", args=[slug]))
    assert statuses == dict.fromkeys(VIEWERS, 200)


def test_open_community_content_except_technical_admin_and_auditor(matrix):
    slug = matrix["communities"]["open"].slug
    statuses = _statuses(matrix, reverse("communities:members", args=[slug]))
    _expect(statuses, _allowed(*[v for v in VIEWERS if v not in {"technical_admin", "auditor"}]))


# --- View the content of a private community -------------------------------------------------


@pytest.mark.parametrize("kind", ["private", "secret"])
def test_private_content_only_for_members_and_granted_admin(matrix, kind):
    slug = matrix["communities"][kind].slug
    statuses = _statuses(matrix, reverse("communities:members", args=[slug]))
    allowed = _allowed(*ROLES, "functional_admin_grant")
    if kind == "private":
        _expect(statuses, allowed)
    else:  # an unlisted invite-only community does not exist for non-members
        refused = {"non_member": 404, "manager": 404, "technical_admin": 404, "auditor": 404}
        expected = {v: 200 if ok else refused.get(v, 403) for v, ok in allowed.items()}
        assert statuses == expected


def test_secret_community_metadata_is_hidden_from_non_members(matrix):
    slug = matrix["communities"]["secret"].slug
    statuses = _statuses(matrix, reverse("communities:detail", args=[slug]))
    hidden = {"non_member", "manager", "technical_admin", "auditor"}
    assert statuses == {viewer: 404 if viewer in hidden else 200 for viewer in VIEWERS}


# --- Join / request membership ---------------------------------------------------------------


@pytest.mark.parametrize("kind", ["open", "private"])
def test_only_non_members_join_or_request(matrix, kind):
    community = matrix["communities"][kind]
    url = reverse("communities:join", args=[community.slug])
    for viewer in ROLES:
        assert matrix["clients"][viewer].post(url).status_code == 302
        assert community.memberships.filter(user=matrix["users"][viewer], role=viewer).exists()
        assert not community.membership_requests.filter(user=matrix["users"][viewer]).exists()
    non_member = matrix["users"]["non_member"]
    assert matrix["clients"]["non_member"].post(url).status_code == 302
    if kind == "open":
        assert community.memberships.filter(user=non_member).exists()
    else:
        assert community.membership_requests.filter(user=non_member, status="pending").exists()


# --- Manage members, roles, requests ---------------------------------------------------------


@pytest.mark.parametrize("name", ["manage_members", "manage_requests", "manage_invitations"])
def test_manage_members_and_requests(matrix, name):
    slug = matrix["communities"]["private"].slug
    statuses = _statuses(matrix, reverse(f"communities:{name}", args=[slug]))
    _expect(statuses, _allowed("animator", "owner", "functional_admin", "functional_admin_grant"))


def test_only_lead_and_functional_admin_change_roles(matrix):
    community = matrix["communities"]["private"]
    target = matrix["users"]["member"]
    url = reverse("communities:manage_member_role", args=[community.slug, target.public_id])
    for viewer in VIEWERS:
        response = matrix["clients"][viewer].post(url, {"role": "contributor"})
        allowed = viewer in {"owner", "functional_admin", "functional_admin_grant"}
        assert response.status_code == (302 if allowed else 403), viewer
    membership = CommunityMembership.objects.get(community=community, user=target)
    assert membership.role == "contributor"


def test_facilitator_cannot_remove_the_lead(matrix):
    """Footnote 3: a facilitator manages members but not the Facilitator/Lead roles."""
    community = matrix["communities"]["private"]
    lead = matrix["users"]["owner"]
    url = reverse("communities:manage_member_remove", args=[community.slug, lead.public_id])
    matrix["clients"]["animator"].post(url)
    assert community.memberships.filter(user=lead, role="owner").exists()


# --- Configure / archive the community -------------------------------------------------------


def test_configure_only_lead_and_functional_admin(matrix):
    slug = matrix["communities"]["open"].slug
    statuses = _statuses(matrix, reverse("communities:manage_settings", args=[slug]))
    _expect(statuses, _allowed("owner", "functional_admin", "functional_admin_grant"))


@pytest.mark.parametrize(
    "viewer",
    [v for v in VIEWERS if v not in {"owner", "functional_admin", "functional_admin_grant"}],
)
def test_archive_refused(matrix, viewer):
    community = matrix["communities"]["open"]
    url = reverse("communities:manage_status", args=[community.slug])
    response = matrix["clients"][viewer].post(url, {"action": "archive"})
    assert response.status_code == 403
    community.refresh_from_db()
    assert community.status == Community.Status.ACTIVE


@pytest.mark.parametrize("viewer", ["owner", "functional_admin"])
def test_archive_allowed(matrix, viewer):
    community = matrix["communities"]["open"]
    url = reverse("communities:manage_status", args=[community.slug])
    matrix["clients"][viewer].post(url, {"action": "archive"})
    community.refresh_from_db()
    assert community.status == Community.Status.ARCHIVED


def test_aggregated_statistics():
    pytest.skip("Aggregated community statistics arrive in a later work package (L9).")


# --- A private community never appears in a result for a non-member ---------------------------


CATALOGUE_QUERIES = [
    {},
    {"q": "guild"},
    {"q": "Secret"},
    {"access_mode": "invite"},
    {"category": "test-category"},
    {"mine": "on"},
    {"sort": "name"},
]


@pytest.mark.parametrize("query", CATALOGUE_QUERIES)
@pytest.mark.parametrize("viewer", ["non_member", "manager", "technical_admin", "auditor"])
def test_private_community_never_listed_for_non_members(matrix, viewer, query):
    response = matrix["clients"][viewer].get(reverse("communities:catalogue"), query)
    assert response.status_code == 200
    names = {community.name for community in response.context["page_obj"]}
    assert "Secret guild" not in names
    assert "Secret guild" not in response.content.decode()


@pytest.mark.parametrize("query", [{"q": "Secret"}, {"access_mode": "invite"}, {"mine": "on"}])
def test_members_find_their_private_community(matrix, query):
    response = matrix["clients"]["member"].get(reverse("communities:catalogue"), query)
    assert "Secret guild" in {community.name for community in response.context["page_obj"]}
