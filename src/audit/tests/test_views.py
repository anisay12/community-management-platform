from datetime import date, timedelta

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone, translation

from accounts.roles import Role
from audit.forms import AuditFilterForm
from audit.models import AuditEvent
from audit.templatetags.audit_tags import action_category, can_view_audit_log
from communities.models import Community, CommunityCategory
from core.tests.helpers import assert_single_h1

pytestmark = pytest.mark.django_db

LIST_URL = "/audit/"


def _user(make_user, email, *roles, **extra):
    user = make_user(email, **extra)
    for role in roles:
        user.groups.add(Group.objects.get(name=role))
    return user


@pytest.fixture
def auditor(make_user):
    return _user(make_user, "auditor@example.com", Role.AUDITOR)


@pytest.fixture
def auditor_client(client, auditor, verified_login):
    return verified_login(client, auditor)


@pytest.fixture
def events(make_user):
    actor = make_user("actor@example.com", first_name="Alice", last_name="Martin")
    base = timezone.now()
    community = Community.objects.create(
        name="Audited", slug="audited", tagline="t", category=CommunityCategory.objects.first()
    )
    return {
        "auth": AuditEvent.objects.create(
            actor=actor, action="auth.login", target_type="user", target_id="1", created_at=base
        ),
        "user": AuditEvent.objects.create(
            actor=None,
            action="user.status_changed",
            target_type="user",
            target_id="user-42",
            changes={"note": "<script>alert(1)</script>"},
            ip_hash="abc123",
            request_id="req-1",
            community=community,
            created_at=base - timedelta(minutes=1),
        ),
    }


def _detail(event):
    return reverse("audit:event_detail", args=[event.pk])


def test_urls():
    assert reverse("audit:event_list") == LIST_URL
    assert reverse("audit:event_detail", args=[5]) == "/audit/5/"


def test_anonymous_gets_404(client, events):
    assert client.get(LIST_URL).status_code == 404
    assert client.get(_detail(events["auth"])).status_code == 404


def test_employee_and_community_creator_get_404(client, make_user, events):
    for index, role in enumerate((Role.EMPLOYEE, Role.COMMUNITY_CREATOR)):
        user = _user(make_user, f"u{index}@example.com", role)
        client.force_login(user)
        assert client.get(LIST_URL).status_code == 404
        assert client.get(_detail(events["auth"])).status_code == 404


def test_auditor_sees_everything(auditor_client, events):
    response = auditor_client.get(LIST_URL)
    assert response.status_code == 200
    content = response.content.decode()
    assert "auth.login" in content
    assert "user.status_changed" in content
    assert response["Cache-Control"].startswith("max-age=0")
    assert "no-cache" in response["Cache-Control"]


def test_functional_admin_scope(client, functional_admin, verified_login, events):
    verified_login(client, functional_admin)
    response = client.get(LIST_URL)
    assert response.status_code == 200
    content = response.content.decode()
    assert "user.status_changed" in content
    assert "auth.login" not in content
    assert client.get(_detail(events["user"])).status_code == 200
    assert client.get(_detail(events["auth"])).status_code == 404


def test_technical_admin_scope(client, make_user, verified_login, events):
    admin = _user(make_user, "tadmin@example.com", Role.TECHNICAL_ADMIN)
    verified_login(client, admin)
    content = client.get(LIST_URL).content.decode()
    assert "auth.login" in content
    assert "user.status_changed" not in content
    assert client.get(_detail(events["auth"])).status_code == 200
    assert client.get(_detail(events["user"])).status_code == 404


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_unsafe_methods_are_405(auditor_client, events, method):
    before = AuditEvent.objects.count()
    for url in (LIST_URL, _detail(events["auth"])):
        assert getattr(auditor_client, method)(url).status_code == 405
    assert AuditEvent.objects.count() == before


def test_filters_narrow_the_list(auditor_client, events):
    content = auditor_client.get(LIST_URL, {"action": "auth.login"}).content.decode()
    assert "<td>user.status_changed</td>" not in content
    content = auditor_client.get(LIST_URL, {"actor": "martin"}).content.decode()
    assert "<td>auth.login</td>" in content
    assert "<td>user.status_changed</td>" not in content
    content = auditor_client.get(LIST_URL, {"target": "user-42"}).content.decode()
    assert "<td>user.status_changed</td>" in content
    assert "<td>auth.login</td>" not in content
    today = timezone.localdate().isoformat()
    content = auditor_client.get(LIST_URL, {"date_from": today, "date_to": today}).content.decode()
    assert "<td>auth.login</td>" in content


def test_empty_state(auditor_client, events):
    response = auditor_client.get(LIST_URL, {"target": "nothing-here"})
    assert "No audit event matches these filters." in response.content.decode()


def test_inverted_dates_show_error_and_unfiltered_list(auditor_client, events):
    response = auditor_client.get(LIST_URL, {"date_from": "2024-02-01", "date_to": "2024-01-01"})
    content = response.content.decode()
    assert response.status_code == 200
    assert "There is a problem" in content
    assert "start date must not be after the end date" in content
    assert "auth.login" in content


@pytest.mark.parametrize("value", ["9999-12-31", "0001-01-01", "1999-12-31", "2101-01-01"])
def test_out_of_range_dates_do_not_crash(auditor_client, events, value):
    for field in ("date_from", "date_to"):
        response = auditor_client.get(LIST_URL, {field: value})
        assert response.status_code == 200
        assert "There is a problem" in response.content.decode()


def test_form_accepts_in_range_dates():
    form = AuditFilterForm({"date_from": "2024-01-01", "date_to": "2024-01-02"}, actions=[])
    assert form.is_valid()
    assert form.cleaned_data["date_from"] == date(2024, 1, 1)


def test_form_rejects_unknown_action():
    form = AuditFilterForm({"action": "nope"}, actions=["auth.login"])
    assert not form.is_valid()


def test_action_choices_only_in_scope(client, functional_admin, verified_login, events):
    verified_login(client, functional_admin)
    content = client.get(LIST_URL).content.decode()
    assert '<option value="user.status_changed"' in content
    assert '<option value="auth.login"' not in content


def test_pagination_and_query_count(auditor_client, auditor, django_assert_max_num_queries):
    AuditEvent.objects.bulk_create(
        [AuditEvent(action="user.updated", target_type="user", target_id=str(i)) for i in range(60)]
    )
    total = AuditEvent.objects.count()
    with django_assert_max_num_queries(12):
        response = auditor_client.get(LIST_URL, {"action": "user.updated"})
    assert len(response.context["page_obj"]) == 50
    page2 = auditor_client.get(LIST_URL, {"action": "user.updated", "page": 2})
    assert len(page2.context["page_obj"]) == 10
    assert total >= 60
    assert "action=user.updated" in page2.context["querystring"]
    assert "action=user.updated&amp;page=1" in page2.content.decode()


def test_detail_shows_fields_and_escapes_changes(auditor_client, events):
    content = auditor_client.get(_detail(events["user"])).content.decode()
    assert "System" in content
    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;" in content
    assert "abc123" in content
    assert "req-1" in content
    assert "Functional" in content
    assert reverse("audit:event_list") in content


def test_detail_actor_and_technical_category(auditor_client, events):
    content = auditor_client.get(_detail(events["auth"])).content.decode()
    assert "Alice Martin" in content
    assert "actor@example.com" in content
    assert "Technical and security" in content


def test_detail_anonymized_actor(auditor_client, make_user):
    actor = make_user("gone@example.com", first_name="Gone", last_name="Person")
    actor.anonymized_at = timezone.now()
    actor.save()
    event = AuditEvent.objects.create(
        actor=actor, action="user.updated", target_type="user", target_id="1"
    )
    content = auditor_client.get(_detail(event)).content.decode()
    assert "Former employee" in content
    assert "Gone Person" not in content


def test_auditor_without_verified_mfa_is_sent_to_mfa(client, auditor):
    client.force_login(auditor)
    response = client.get(LIST_URL)
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("accounts:mfa_setup"))


def test_header_link(client, auditor, make_user, verified_login):
    verified_login(client, auditor)
    assert 'href="/audit/"' in client.get("/").content.decode()
    client.logout()
    employee = _user(make_user, "emp@example.com", Role.EMPLOYEE)
    client.force_login(employee)
    assert 'href="/audit/"' not in client.get("/").content.decode()


def test_french_rendering(auditor_client, events):
    response = auditor_client.get(LIST_URL, headers={"accept-language": "fr"})
    content = response.content.decode()
    assert "Journal d'audit" in content or "Journal d&#x27;audit" in content


def test_template_tags(auditor, make_user):
    assert can_view_audit_log(auditor)
    assert not can_view_audit_log(make_user("x@example.com"))
    with translation.override("en"):
        assert action_category("auth.login") == "Technical and security"
        assert action_category("user.updated") == "Functional"
    with translation.override("fr"):
        assert action_category("user.updated") == "Fonctionnel"


def test_audit_pages_use_design_system_patterns(auditor_client, events):
    response = auditor_client.get(LIST_URL)
    assert_single_h1(response)
    content = response.content.decode()
    assert '<caption class="visually-hidden">' in content
    assert "table-responsive" in content
    assert "tl-toolbar" in content and "tl-section" in content
    detail = auditor_client.get(_detail(events["user"]))
    assert_single_h1(detail)
    html = detail.content.decode()
    assert 'aria-label="Breadcrumb"' in html
    assert "<dl" in html and "tl-section" in html


def test_empty_state_component(auditor_client, events):
    content = auditor_client.get(LIST_URL, {"target": "nothing-here"}).content.decode()
    assert "tl-empty-state" in content
    assert "<table" not in content


def test_pagination_component_keeps_filters(auditor_client):
    AuditEvent.objects.bulk_create(
        [AuditEvent(action="user.updated", target_type="user", target_id=str(i)) for i in range(60)]
    )
    content = auditor_client.get(LIST_URL, {"action": "user.updated"}).content.decode()
    assert "page-link" in content
    assert "action=user.updated&amp;page=2" in content


def test_head_requests_return_200(auditor_client, events):
    assert auditor_client.head(LIST_URL).status_code == 200
    assert auditor_client.head(_detail(events["auth"])).status_code == 200


def test_french_ip_hash_label_is_capitalised(auditor_client, events):
    response = auditor_client.get(_detail(events["user"]), headers={"accept-language": "fr"})
    assert "Empreinte de l'adresse IP" in response.content.decode()
    with translation.override("fr"):
        assert (
            str(AuditEvent._meta.get_field("ip_hash").verbose_name) == "empreinte de l'adresse IP"
        )


def test_out_of_range_message_in_english(auditor_client, events):
    content = auditor_client.get(LIST_URL, {"date_from": "1999-12-31"}).content.decode()
    assert "Enter a date between 01/01/2000 and 12/31/2100." in content


def test_out_of_range_message_in_french_uses_local_date_format(auditor_client, events):
    response = auditor_client.get(
        LIST_URL, {"date_from": "1999-12-31"}, headers={"accept-language": "fr"}
    )
    assert (
        "Saisissez une date comprise entre le 01/01/2000 et le 31/12/2100."
        in response.content.decode()
    )
