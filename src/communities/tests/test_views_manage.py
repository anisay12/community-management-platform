"""HTTP tests of the community management pages (settings, members, requests, invitations,
status) by role: member 403, facilitator, lead and functional administrator."""

import pytest
from django.urls import reverse

from audit.models import AuditEvent
from communities.models import (
    Community,
    CommunityInvitation,
    CommunityMembership,
    MembershipRequest,
)
from communities.roles import CommunityRole as Role
from core.tests.helpers import assert_single_h1

pytestmark = pytest.mark.django_db

SLUG = "python-guild"


def url(name, *args):
    return reverse(f"communities:{name}", args=[SLUG, *args])


@pytest.fixture
def community(make_community):
    # The conftest ``add_member`` does not maintain the counter: start from a positive one.
    return make_community("Python guild", access_mode=Community.AccessMode.REQUEST, member_count=10)


@pytest.fixture
def lead(make_user, community, add_member):
    user = make_user("lead@example.com", first_name="Lena", last_name="Lead")
    add_member(community, user, Role.OWNER)
    return user


@pytest.fixture
def facilitator(make_user, community, add_member):
    user = make_user("anim@example.com", first_name="Ana", last_name="Facilitator")
    add_member(community, user, Role.ANIMATOR)
    return user


@pytest.fixture
def member(make_user, community, add_member):
    user = make_user("member@example.com", first_name="Max", last_name="Member")
    add_member(community, user, Role.MEMBER)
    return user


@pytest.fixture
def login(client):
    def _login(user):
        client.force_login(user)
        return client

    return _login


@pytest.fixture
def admin_client(client, functional_admin, verified_login):
    return verified_login(client, functional_admin)


def _membership(community, user):
    return CommunityMembership.objects.get(community=community, user=user)


def _messages(response):
    return [str(m) for m in response.context["messages"]] if response.context else []


# --- access control --------------------------------------------------------------------------

PAGES = ["manage_settings", "manage_members", "manage_requests", "manage_invitations"]


@pytest.mark.parametrize("page", PAGES)
def test_member_gets_403(login, member, page):
    assert login(member).get(url(page)).status_code == 403


@pytest.mark.parametrize("page", PAGES)
def test_invisible_community_is_404(login, make_user, make_community, page):
    make_community("Secret", slug=SLUG, access_mode=Community.AccessMode.INVITE)
    outsider = make_user("out@example.com")
    assert login(outsider).get(url(page)).status_code == 404


@pytest.mark.parametrize("page", PAGES)
def test_anonymous_redirected(client, community, page):
    assert client.get(url(page)).status_code == 302


def test_facilitator_cannot_configure(login, facilitator):
    client = login(facilitator)
    assert client.get(url("manage_settings")).status_code == 403
    for page in PAGES[1:]:
        response = client.get(url(page))
        assert response.status_code == 200
        assert_single_h1(response)


@pytest.mark.parametrize("page", PAGES)
def test_lead_sees_every_page(login, lead, page):
    assert login(lead).get(url(page)).status_code == 200


@pytest.mark.parametrize("page", PAGES)
def test_admin_sees_every_page(admin_client, community, page):
    assert admin_client.get(url(page)).status_code == 200


def test_manage_link_on_community_page(login, lead, member, facilitator):
    page = url("detail")
    assert url("manage_settings") in login(lead).get(page).content.decode()
    assert url("manage_members") in login(facilitator).get(page).content.decode()
    assert "/manage/" not in login(member).get(page).content.decode()


# --- settings --------------------------------------------------------------------------------


def _settings_data(community, **extra):
    data = {
        "name": community.name,
        "category": community.category.pk,
        "tagline": community.tagline,
        "description": "## Hello",
        "rules": "",
        "objectives": "",
        "access_mode": community.access_mode,
    }
    data.update(extra)
    return data


def test_lead_updates_settings(login, lead, community):
    response = login(lead).post(
        url("manage_settings"), _settings_data(community, tagline="New tagline", listed="on")
    )
    assert response.status_code == 302
    community.refresh_from_db()
    assert community.tagline == "New tagline"
    assert community.listed is True
    assert AuditEvent.objects.filter(action="community.updated").exists()


def test_settings_name_taken_shows_error(login, lead, community, make_community):
    make_community("Rust guild")
    response = login(lead).post(
        url("manage_settings"), _settings_data(community, name="rust GUILD")
    )
    assert response.status_code == 200
    assert "already exists" in response.content.decode()


def test_settings_read_only_when_suspended(login, lead, community):
    Community.objects.filter(pk=community.pk).update(status=Community.Status.SUSPENDED)
    client = login(lead)
    response = client.get(url("manage_settings"))
    assert response.status_code == 200
    assert response.context["read_only"] is True
    response = client.post(url("manage_settings"), _settings_data(community, tagline="Changed"))
    assert response.status_code == 200
    community.refresh_from_db()
    assert community.tagline == "Everything Python"


# --- members ---------------------------------------------------------------------------------


def test_members_search(login, lead, member, facilitator):
    response = login(lead).get(url("manage_members"), {"q": "Max"})
    names = [m.user.first_name for m in response.context["page_obj"]]
    assert names == ["Max"]
    assert "member@example.com" not in response.content.decode()


def test_lead_changes_role_with_audit(login, lead, member, community):
    response = login(lead).post(
        url("manage_member_role", member.public_id), {"role": Role.MODERATOR}
    )
    assert response.status_code == 302
    assert _membership(community, member).role == Role.MODERATOR
    event = AuditEvent.objects.get(action="community.role_changed")
    assert event.community_id == community.pk


def test_facilitator_cannot_change_roles(login, facilitator, member, community):
    client = login(facilitator)
    response = client.get(url("manage_members"))
    assert response.context["can_change_roles"] is False
    response = client.post(url("manage_member_role", member.public_id), {"role": Role.EXPERT})
    assert response.status_code == 403
    assert _membership(community, member).role == Role.MEMBER


def test_last_owner_protection(login, lead, community):
    response = login(lead).post(
        url("manage_member_role", lead.public_id), {"role": Role.MEMBER}, follow=True
    )
    assert _membership(community, lead).role == Role.OWNER
    assert any("at least one lead" in m for m in _messages(response))
    response = login(lead).post(url("manage_member_remove", lead.public_id), follow=True)
    assert _membership(community, lead).role == Role.OWNER
    assert any("at least one lead" in m for m in _messages(response))


def test_invalid_role_rejected(login, lead, member, community):
    response = login(lead).post(url("manage_member_role", member.public_id), {"role": "king"})
    assert response.status_code == 302
    assert _membership(community, member).role == Role.MEMBER


def test_facilitator_removes_member_but_not_lead(login, facilitator, member, lead, community):
    client = login(facilitator)
    response = client.post(url("manage_member_remove", member.public_id))
    assert response.status_code == 302
    assert not CommunityMembership.objects.filter(community=community, user=member).exists()
    response = client.post(url("manage_member_remove", lead.public_id), follow=True)
    assert CommunityMembership.objects.filter(community=community, user=lead).exists()
    assert _messages(response)


def test_member_actions_by_member_403(login, member, lead):
    client = login(member)
    assert client.post(url("manage_member_remove", lead.public_id)).status_code == 403


def test_unknown_member_404(login, lead, make_user):
    stranger = make_user("x@example.com")
    assert login(lead).post(url("manage_member_remove", stranger.public_id)).status_code == 404


def test_actions_are_post_only(login, lead, member):
    assert login(lead).get(url("manage_member_remove", member.public_id)).status_code == 405


# --- requests --------------------------------------------------------------------------------


@pytest.fixture
def pending(make_user, community):
    applicant = make_user("app@example.com", first_name="Al", last_name="Applicant")
    return MembershipRequest.objects.create(community=community, user=applicant, message="Hi")


def test_requests_listed_pending_first(login, facilitator, pending, community, make_user):
    old = MembershipRequest.objects.create(
        community=community,
        user=make_user("old@example.com"),
        status=MembershipRequest.Status.REJECTED,
    )
    response = login(facilitator).get(url("manage_requests"))
    assert list(response.context["requests"]) == [pending, old]


def test_accept_request(login, facilitator, pending, community):
    response = login(facilitator).post(
        url("manage_request_decide", pending.public_id), {"decision": "accept"}
    )
    assert response.status_code == 302
    assert CommunityMembership.objects.filter(community=community, user=pending.user).exists()


def test_reject_request_with_note(login, facilitator, pending):
    login(facilitator).post(
        url("manage_request_decide", pending.public_id), {"decision": "reject", "note": "Sorry"}
    )
    pending.refresh_from_db()
    assert pending.status == MembershipRequest.Status.REJECTED
    assert pending.decision_note == "Sorry"


def test_decide_twice_shows_message(login, facilitator, pending):
    client = login(facilitator)
    client.post(url("manage_request_decide", pending.public_id), {"decision": "reject"})
    response = client.post(
        url("manage_request_decide", pending.public_id), {"decision": "accept"}, follow=True
    )
    assert _messages(response)


def test_decide_bad_decision(login, facilitator, pending):
    response = login(facilitator).post(
        url("manage_request_decide", pending.public_id), {"decision": "maybe"}
    )
    assert response.status_code == 400


def test_member_cannot_decide(login, member, pending):
    response = login(member).post(
        url("manage_request_decide", pending.public_id), {"decision": "accept"}
    )
    assert response.status_code == 403


# --- invitations -----------------------------------------------------------------------------


def test_invite_by_email(login, facilitator, make_user, community):
    invitee = make_user("Guest@Example.com")
    response = login(facilitator).post(
        url("manage_invitations"), {"email": "guest@example.com", "role": Role.EXPERT}
    )
    assert response.status_code == 302
    invitation = CommunityInvitation.objects.get(community=community)
    assert invitation.invited_user == invitee
    assert invitation.role == Role.EXPERT


def test_invite_unknown_email(login, facilitator, community):
    response = login(facilitator).post(
        url("manage_invitations"), {"email": "nobody@example.com", "role": Role.MEMBER}
    )
    assert response.status_code == 200
    assert response.context["form"].errors
    assert not CommunityInvitation.objects.exists()


def test_facilitator_cannot_offer_facilitator(login, facilitator, make_user):
    make_user("guest@example.com")
    client = login(facilitator)
    response = client.get(url("manage_invitations"))
    roles = [value for value, _ in response.context["form"].fields["role"].choices]
    assert Role.ANIMATOR not in roles and Role.OWNER not in roles
    response = client.post(
        url("manage_invitations"), {"email": "guest@example.com", "role": Role.ANIMATOR}
    )
    assert response.status_code == 200
    assert not CommunityInvitation.objects.exists()


def test_lead_may_offer_facilitator(login, lead):
    response = login(lead).get(url("manage_invitations"))
    roles = [value for value, _ in response.context["form"].fields["role"].choices]
    assert Role.ANIMATOR in roles and Role.OWNER not in roles


def test_invite_existing_member_error(login, lead, member):
    response = login(lead).post(
        url("manage_invitations"), {"email": member.email, "role": Role.MEMBER}
    )
    assert response.status_code == 200
    assert "already a member" in response.content.decode()


def test_revoke_invitation(login, lead, make_user, community):
    invitation = CommunityInvitation.objects.create(
        community=community, invited_user=make_user("g@example.com"), invited_by=lead
    )
    client = login(lead)
    assert client.get(url("manage_invitations")).context["invitations"][0] == invitation
    response = client.post(url("manage_invitation_revoke", invitation.public_id))
    assert response.status_code == 302
    invitation.refresh_from_db()
    assert invitation.status == CommunityInvitation.Status.REVOKED
    response = client.post(url("manage_invitation_revoke", invitation.public_id), follow=True)
    assert _messages(response)


# --- status ----------------------------------------------------------------------------------


def test_admin_suspends_and_reactivates(admin_client, community):
    response = admin_client.post(url("manage_status"), {"action": "suspend"})
    assert response.status_code == 302
    community.refresh_from_db()
    assert community.status == Community.Status.SUSPENDED
    assert AuditEvent.objects.filter(action="community.suspended").exists()
    admin_client.post(url("manage_status"), {"action": "reactivate"})
    community.refresh_from_db()
    assert community.status == Community.Status.ACTIVE


def test_lead_cannot_suspend(login, lead, community):
    response = login(lead).post(url("manage_status"), {"action": "suspend"})
    assert response.status_code == 403
    community.refresh_from_db()
    assert community.status == Community.Status.ACTIVE


def test_lead_archives_and_unarchives(login, lead, community):
    client = login(lead)
    client.post(url("manage_status"), {"action": "archive"})
    community.refresh_from_db()
    assert community.status == Community.Status.ARCHIVED
    assert AuditEvent.objects.filter(action="community.archived").exists()
    client.post(url("manage_status"), {"action": "unarchive"})
    community.refresh_from_db()
    assert community.status == Community.Status.ACTIVE


def test_status_confirmation_page_without_js(login, lead):
    response = login(lead).get(url("manage_status"), {"action": "archive"})
    assert response.status_code == 200
    assert_single_h1(response)
    assert login(lead).get(url("manage_status"), {"action": "bogus"}).status_code == 400


def test_status_invalid_transition_message(login, lead, community):
    response = login(lead).post(url("manage_status"), {"action": "unarchive"}, follow=True)
    assert _messages(response)


def test_member_cannot_archive(login, member):
    assert login(member).post(url("manage_status"), {"action": "archive"}).status_code == 403


def test_suspended_community_read_only_in_members_ui(login, lead, member, community):
    Community.objects.filter(pk=community.pk).update(status=Community.Status.SUSPENDED)
    client = login(lead)
    response = client.get(url("manage_members"))
    assert response.context["read_only"] is True
    assert url("manage_member_remove", member.public_id) not in response.content.decode()
    response = client.post(url("manage_member_remove", member.public_id), follow=True)
    assert CommunityMembership.objects.filter(community=community, user=member).exists()
    assert _messages(response)


def test_settings_page_offers_lead_status_actions(login, lead):
    body = login(lead).get(url("manage_settings")).content.decode()
    assert 'value="archive"' in body and 'value="suspend"' not in body


def test_settings_page_offers_admin_status_actions(admin_client, community):
    body = admin_client.get(url("manage_settings")).content.decode()
    assert 'value="suspend"' in body
