import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import translation

from audit.models import AuditEvent, AuditImmutableError
from audit.services import record
from communities.models import (
    AdminAccessGrant,
    Community,
    CommunityCategory,
    CommunityInvitation,
    CommunityMembership,
    MembershipRequest,
)
from communities.roles import ROLE_RANK, CommunityRole, role_at_least

pytestmark = pytest.mark.django_db

OFFICIAL_CATEGORIES = [
    "Data Engineering",
    "Data Science et Intelligence Artificielle",
    "Cloud et Architecture",
    "DevOps et Platform Engineering",
    "Data Governance et Data Quality",
    "Business Intelligence et Analytics",
    "Cybersécurité",
    "Développement logiciel",
    "Agilité et gestion de projet",
    "Expertise métier",
    "Préparation aux certifications",
    "Innovation et veille technologique",
]


def test_seeded_categories_are_the_official_twelve_in_order():
    seeded = CommunityCategory.objects.exclude(slug="test-category")
    assert list(seeded.values_list("name", flat=True)) == OFFICIAL_CATEGORIES
    slugs = list(seeded.values_list("slug", flat=True))
    assert "cybersecurite" in slugs and "developpement-logiciel" in slugs
    assert all(slug.isascii() for slug in slugs)
    assert all(icon for icon in seeded.values_list("icon", flat=True))


def test_seed_migration_is_idempotent_and_reversible():
    from importlib import import_module

    from django.apps import apps

    seed_module = import_module("communities.migrations.0002_seed_categories")
    seed_module.seed(apps, None)
    assert CommunityCategory.objects.count() == 12
    seed_module.unseed(apps, None)
    assert CommunityCategory.objects.count() == 0
    seed_module.seed(apps, None)
    assert CommunityCategory.objects.count() == 12


def test_unseed_keeps_categories_in_use(make_community):
    from importlib import import_module

    from django.apps import apps

    used = CommunityCategory.objects.get(slug="cloud-et-architecture")
    make_community(category=used)
    import_module("communities.migrations.0002_seed_categories").unseed(apps, None)
    assert set(CommunityCategory.objects.values_list("slug", flat=True)) == {
        "cloud-et-architecture",
        "test-category",
    }


def test_community_defaults(make_community):
    community = make_community()
    assert community.access_mode == Community.AccessMode.OPEN
    assert community.status == Community.Status.ACTIVE
    assert community.listed is False
    assert community.allow_member_uploads is False
    assert community.require_post_review is False
    assert community.member_count == 0
    assert community.public_id is not None
    assert not community.is_read_only


def test_active_name_is_unique_case_insensitively(make_community):
    make_community("Python guild")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_community("PYTHON GUILD", slug="other")


def test_archived_name_can_be_reused(make_community):
    make_community("Python guild", status=Community.Status.ARCHIVED)
    make_community("python guild", slug="python-guild-2")


def test_one_membership_per_user(make_community, make_user, add_member):
    community, user = make_community(), make_user()
    add_member(community, user)
    with pytest.raises(IntegrityError), transaction.atomic():
        add_member(community, user)


def test_two_pending_requests_are_impossible(make_community, make_user):
    community, user = make_community(), make_user()
    MembershipRequest.objects.create(community=community, user=user)
    with pytest.raises(IntegrityError), transaction.atomic():
        MembershipRequest.objects.create(community=community, user=user)


def test_decided_requests_do_not_block_a_new_one(make_community, make_user):
    community, user = make_community(), make_user()
    MembershipRequest.objects.create(
        community=community, user=user, status=MembershipRequest.Status.REJECTED
    )
    MembershipRequest.objects.create(community=community, user=user)


def test_two_pending_invitations_are_impossible(make_community, make_user):
    community, user = make_community(), make_user()
    invitation = CommunityInvitation.objects.create(community=community, invited_user=user)
    assert not invitation.is_expired
    assert (invitation.expires_at - invitation.created_at).days in (13, 14)
    with pytest.raises(IntegrityError), transaction.atomic():
        CommunityInvitation.objects.create(community=community, invited_user=user)


def test_invitation_cannot_grant_ownership(make_community, make_user):
    with pytest.raises(IntegrityError), transaction.atomic():
        CommunityInvitation.objects.create(
            community=make_community(), invited_user=make_user(), role=CommunityRole.OWNER
        )


def test_admin_grant_lasts_one_hour_and_needs_a_reason(make_community, functional_admin):
    from django.core.exceptions import ValidationError

    grant = AdminAccessGrant(community=make_community(), user=functional_admin, reason="short")
    with pytest.raises(ValidationError):
        grant.full_clean()
    grant.reason = "Investigating a reported post"
    grant.full_clean()
    grant.save()
    assert round((grant.expires_at - grant.created_at).total_seconds()) == 3600
    assert AdminAccessGrant.objects.valid().count() == 1


def test_category_protected_from_deletion(make_community, category):
    from django.db.models import ProtectedError

    make_community()
    with pytest.raises(ProtectedError):
        category.delete()


def test_role_rank_and_labels():
    assert [ROLE_RANK[r] for r in CommunityRole] == [1, 2, 3, 4, 5, 6]
    assert role_at_least("owner", "animator")
    assert role_at_least("animator", "animator")
    assert not role_at_least("moderator", "animator")
    assert not role_at_least("bogus", "member")
    with translation.override("en"):
        assert str(CommunityRole.ANIMATOR.label) == "Facilitator"
        assert str(CommunityRole.OWNER.label) == "Lead"
    with translation.override("fr"):
        assert str(CommunityRole.ANIMATOR.label) == "Animateur"
        assert str(CommunityRole.OWNER.label) == "Responsable"
    assert CommunityMembership.Role is CommunityRole


def test_record_accepts_a_community_or_its_id(make_community):
    community = make_community()
    by_object = record(actor=None, action="x.y", target=community, community=community)
    by_id = record(actor=None, action="x.y", target=("thing", "1"), community_id=community.pk)
    for event in (by_object, by_id):
        event.refresh_from_db()
        assert event.community == community
        assert event.community_id == community.pk


def test_audit_event_survives_community_deletion_and_stays_append_only(make_community):
    community = make_community()
    event = record(actor=None, action="x.y", target=community, community=community)
    community.delete()
    event = AuditEvent.objects.get(pk=event.pk)
    assert event.community_id is None
    with pytest.raises(AuditImmutableError):
        event.save()
    with pytest.raises(AuditImmutableError):
        AuditEvent.objects.filter(pk=event.pk).update(action="other")


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_audit_fk_migration_keeps_existing_rows():
    """Rows written with the pre-L3 schema survive the bigint -> foreign key change."""
    executor = MigrationExecutor(connection)
    before = [("audit", "0001_initial"), ("communities", "0002_seed_categories")]
    after = executor.loader.graph.leaf_nodes()
    executor.migrate(before)
    try:
        old_apps = executor.loader.project_state(before).apps
        OldEvent = old_apps.get_model("audit", "AuditEvent")
        Category = old_apps.get_model("communities", "CommunityCategory")
        Community = old_apps.get_model("communities", "Community")
        community = Community.objects.create(
            name="Legacy", slug="legacy", tagline="t", category=Category.objects.first()
        )
        OldEvent.objects.create(action="legacy.none", target_type="t", target_id="1")
        OldEvent.objects.create(
            action="legacy.linked", target_type="t", target_id="2", community_id=community.pk
        )
    finally:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(after)
    events = {e.action: e for e in AuditEvent.objects.filter(action__startswith="legacy.")}
    assert events["legacy.none"].community_id is None
    assert events["legacy.linked"].community.slug == "legacy"
    with pytest.raises(IntegrityError), transaction.atomic():
        record(actor=None, action="x.y", target=("t", "3"), community_id=10**9)
        connection.check_constraints()
