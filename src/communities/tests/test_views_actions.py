"""HTTP tests of member actions: join, leave, requests, invitations and creation."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from accounts.roles import Role as AccountRole
from communities.models import (
    Community,
    CommunityCreationRequest,
    CommunityInvitation,
    CommunityMembership,
    MembershipRequest,
)
from communities.roles import CommunityRole as Role

pytestmark = pytest.mark.django_db

OPEN, REQUEST, INVITE = Community.AccessMode


@pytest.fixture
def employee(make_user):
    return make_user("emp@example.com", first_name="Eve", last_name="Employee")


@pytest.fixture
def employee_client(client, employee):
    client.force_login(employee)
    return client


def _url(name, slug):
    return reverse(f"communities:{name}", args=[slug])


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


def _member_count(community):
    community.refresh_from_db()
    return community.member_count


# --- POST only, visibility ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["join", "leave", "cancel_request"])
def test_actions_reject_get(employee_client, make_community, name):
    make_community("Open", access_mode=OPEN)
    assert employee_client.get(_url(name, "open")).status_code == 405


def test_invitation_respond_rejects_get(employee_client, make_community, employee, make_user):
    community = make_community("Secret", access_mode=INVITE)
    invitation = CommunityInvitation.objects.create(
        community=community, invited_user=employee, invited_by=make_user("x@example.com")
    )
    url = reverse("communities:invitation_respond", args=[invitation.public_id])
    assert employee_client.get(url).status_code == 405


@pytest.mark.parametrize("name", ["join", "leave", "cancel_request"])
def test_actions_on_invisible_community_404(employee_client, make_community, name):
    make_community("Hidden", access_mode=INVITE)
    assert employee_client.post(_url(name, "hidden")).status_code == 404


def test_actions_require_login(client, make_community):
    make_community("Open", access_mode=OPEN)
    response = client.post(_url("join", "open"))
    assert response.status_code == 302
    assert "login" in response.url


# --- join / leave ---------------------------------------------------------------------------


def test_join_open_community(employee_client, make_community, employee):
    community = make_community("Open", access_mode=OPEN)
    response = employee_client.post(_url("join", "open"))
    assert response.status_code == 302
    assert response.url == _url("detail", "open")
    assert CommunityMembership.objects.filter(community=community, user=employee).exists()
    assert _member_count(community) == 1
    assert _messages(response)


def test_join_invite_only_shows_error(employee_client, make_community, employee):
    make_community("Listed", access_mode=INVITE, listed=True)
    response = employee_client.post(_url("join", "listed"))
    assert response.status_code == 302
    assert any("invitation" in m for m in _messages(response))
    assert not CommunityMembership.objects.filter(user=employee).exists()


def test_join_twice_already_member(employee_client, make_community, employee, add_member):
    community = make_community("Open", access_mode=OPEN)
    add_member(community, employee)
    response = employee_client.post(_url("join", "open"))
    assert response.status_code == 302
    assert any("already" in m for m in _messages(response))


def test_join_request_community_creates_request_with_message(
    employee_client, make_community, employee
):
    community = make_community("Req", access_mode=REQUEST)
    response = employee_client.post(_url("join", "req"), {"message": "Let me in"})
    assert response.status_code == 302
    request = MembershipRequest.objects.get(community=community, user=employee)
    assert request.message == "Let me in"
    assert request.status == MembershipRequest.Status.PENDING
    page = employee_client.get(_url("detail", "req")).content.decode()
    assert _url("cancel_request", "req") in page


def test_duplicate_request_is_idempotent(employee_client, make_community, employee):
    make_community("Req", access_mode=REQUEST)
    employee_client.post(_url("join", "req"), {"message": "one"})
    response = employee_client.post(_url("join", "req"), {"message": "two"})
    assert response.status_code == 302
    assert MembershipRequest.objects.filter(user=employee).count() == 1
    assert _messages(response)


def test_join_request_message_too_long(employee_client, make_community, employee):
    make_community("Req", access_mode=REQUEST)
    response = employee_client.post(_url("join", "req"), {"message": "x" * 501})
    assert response.status_code == 302
    assert not MembershipRequest.objects.filter(user=employee).exists()
    assert _messages(response)


def test_cancel_request(employee_client, make_community, employee):
    community = make_community("Req", access_mode=REQUEST)
    request = MembershipRequest.objects.create(community=community, user=employee)
    response = employee_client.post(_url("cancel_request", "req"))
    assert response.status_code == 302
    request.refresh_from_db()
    assert request.status == MembershipRequest.Status.CANCELLED


def test_cancel_without_pending_request(employee_client, make_community):
    make_community("Req", access_mode=REQUEST)
    response = employee_client.post(_url("cancel_request", "req"))
    assert response.status_code == 302
    assert _messages(response)


def test_leave(employee_client, make_community, employee, add_member, make_user):
    community = make_community("Open", access_mode=OPEN)
    add_member(community, make_user("own@example.com"), Role.OWNER)
    add_member(community, employee)
    response = employee_client.post(_url("leave", "open"))
    assert response.status_code == 302
    assert not CommunityMembership.objects.filter(user=employee).exists()
    assert _member_count(community) == 1


def test_last_owner_cannot_leave(employee_client, make_community, employee, add_member):
    community = make_community("Open", access_mode=OPEN)
    add_member(community, employee, Role.OWNER)
    response = employee_client.post(_url("leave", "open"))
    assert response.status_code == 302
    assert CommunityMembership.objects.filter(user=employee).exists()
    assert _messages(response)


def test_leave_not_member(employee_client, make_community):
    make_community("Open", access_mode=OPEN)
    response = employee_client.post(_url("leave", "open"))
    assert response.status_code == 302
    assert any("not a member" in m for m in _messages(response))


def test_detail_shows_join_and_leave_buttons(employee_client, make_community, employee, add_member):
    community = make_community("Open", access_mode=OPEN)
    page = employee_client.get(_url("detail", "open")).content.decode()
    assert _url("join", "open") in page
    add_member(community, employee)
    page = employee_client.get(_url("detail", "open")).content.decode()
    assert _url("leave", "open") in page


def test_detail_request_shows_message_form(employee_client, make_community):
    make_community("Req", access_mode=REQUEST)
    page = employee_client.get(_url("detail", "req")).content.decode()
    assert 'name="message"' in page
    assert 'maxlength="500"' in page


# --- invitations ----------------------------------------------------------------------------


@pytest.fixture
def invitation(make_community, employee, make_user, add_member):
    community = make_community("Secret", access_mode=INVITE)
    inviter = make_user("own@example.com")
    add_member(community, inviter, Role.OWNER)
    return CommunityInvitation.objects.create(
        community=community, invited_user=employee, invited_by=inviter, role=Role.EXPERT
    )


def _respond(client, invitation, decision):
    url = reverse("communities:invitation_respond", args=[invitation.public_id])
    return client.post(url, {"decision": decision})


def test_my_invitations_lists_pending(employee_client, invitation):
    response = employee_client.get(reverse("communities:my_invitations"))
    assert response.status_code == 200
    assert "Secret" in response.content.decode()
    assert list(response.context["invitations"]) == [invitation]


def test_my_invitations_hides_inactive_communities(employee_client, invitation):
    Community.objects.filter(pk=invitation.community_id).update(status=Community.Status.ARCHIVED)
    response = employee_client.get(reverse("communities:my_invitations"))
    assert list(response.context["invitations"]) == []


def test_my_invitations_is_capped(employee_client, employee, make_community, make_user):
    inviter = make_user("own@example.com")
    for index in range(55):
        community = make_community(f"Club {index}", access_mode=INVITE)
        CommunityInvitation.objects.create(
            community=community, invited_user=employee, invited_by=inviter
        )
    response = employee_client.get(reverse("communities:my_invitations"))
    assert len(response.context["invitations"]) == 50


def test_detail_for_invited_user_shows_respond(employee_client, invitation):
    Community.objects.filter(pk=invitation.community_id).update(listed=True)
    page = employee_client.get(_url("detail", "secret")).content.decode()
    assert reverse("communities:invitation_respond", args=[invitation.public_id]) in page


def test_accept_invitation(employee_client, invitation, employee):
    response = _respond(employee_client, invitation, "accept")
    assert response.status_code == 302
    assert response.url == _url("detail", "secret")
    membership = CommunityMembership.objects.get(user=employee)
    assert membership.role == Role.EXPERT
    assert _member_count(invitation.community) == 2


def test_decline_invitation(employee_client, invitation):
    response = _respond(employee_client, invitation, "decline")
    assert response.status_code == 302
    assert response.url == reverse("communities:my_invitations")
    invitation.refresh_from_db()
    assert invitation.status == CommunityInvitation.Status.DECLINED


def test_expired_invitation(employee_client, invitation, employee):
    CommunityInvitation.objects.filter(pk=invitation.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    response = _respond(employee_client, invitation, "accept")
    assert response.status_code == 302
    assert any("expired" in m for m in _messages(response))
    assert not CommunityMembership.objects.filter(user=employee).exists()


def test_invitation_of_someone_else_404(client, invitation, make_user):
    client.force_login(make_user("other@example.com"))
    assert _respond(client, invitation, "accept").status_code == 404


def test_invalid_decision(employee_client, invitation):
    assert _respond(employee_client, invitation, "maybe").status_code == 400


# --- creation -------------------------------------------------------------------------------


@pytest.fixture
def creator_client(client, employee):
    employee.groups.add(Group.objects.get(name=AccountRole.COMMUNITY_CREATOR))
    client.force_login(employee)
    return client


def test_create_form_for_creator(creator_client):
    response = creator_client.get(reverse("communities:create"))
    assert response.status_code == 200
    assert "form" in response.context


def test_create_redirects_non_creator_to_request(employee_client):
    response = employee_client.get(reverse("communities:create"))
    assert response.status_code == 302
    assert response.url == reverse("communities:creation_request")


def test_create_community(creator_client, category, employee):
    response = creator_client.post(
        reverse("communities:create"),
        {
            "name": "New guild",
            "category": category.pk,
            "tagline": "Hello",
            "description": "Desc",
            "access_mode": REQUEST,
            "listed": "",
        },
    )
    assert response.status_code == 302
    community = Community.objects.get(name="New guild")
    assert response.url == _url("detail", community.slug)
    assert CommunityMembership.objects.get(community=community).user == employee


def test_create_duplicate_name(creator_client, category, make_community):
    make_community("Taken")
    response = creator_client.post(
        reverse("communities:create"),
        {"name": "taken", "category": category.pk, "tagline": "x", "access_mode": OPEN},
    )
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()


def test_create_rejects_inactive_category(creator_client, category):
    category.is_active = False
    category.save()
    response = creator_client.post(
        reverse("communities:create"),
        {"name": "N", "category": category.pk, "tagline": "x", "access_mode": OPEN},
    )
    assert response.status_code == 200
    assert "category" in response.context["form"].errors


def test_creation_request(employee_client, category, employee):
    response = employee_client.get(reverse("communities:creation_request"))
    assert response.status_code == 200
    response = employee_client.post(
        reverse("communities:creation_request"),
        {"name": "Wished", "category": category.pk, "justification": "Because"},
    )
    assert response.status_code == 302
    assert CommunityCreationRequest.objects.get(requester=employee).name == "Wished"


def test_creation_request_duplicate_name(employee_client, category, make_community):
    make_community("Taken")
    response = employee_client.post(
        reverse("communities:creation_request"),
        {"name": "Taken", "category": category.pk, "justification": "Because"},
    )
    assert response.status_code == 200
    assert response.context["form"].non_field_errors()


def test_creator_on_request_page_goes_to_create(creator_client):
    response = creator_client.get(reverse("communities:creation_request"))
    assert response.status_code == 302
    assert response.url == reverse("communities:create")
