"""``policies.can_view_document`` and ``selectors.visible_documents_q`` must agree."""

import itertools

import pytest

from communities.models import Community, CommunityMembership
from documents import policies
from documents.models import Document, DocumentVersion

Role = CommunityMembership.Role
Scan = DocumentVersion.ScanStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(make_user, make_community, add_member, make_document, functional_admin):
    owner = make_user("owner@example.com")
    member = make_user("member@example.com")
    expert = make_user("expert@example.com")
    moderator = make_user("moderator@example.com")
    outsider = make_user("outsider@example.com")
    communities = [
        make_community("Open", access_mode=Community.AccessMode.OPEN),
        make_community("Request", access_mode=Community.AccessMode.REQUEST),
        make_community("Invite", access_mode=Community.AccessMode.INVITE),
    ]
    documents = []
    for community in communities:
        add_member(community, owner, Role.CONTRIBUTOR)
        add_member(community, member, Role.MEMBER)
        add_member(community, expert, Role.EXPERT)
        add_member(community, moderator, Role.MODERATOR)
        for status, scan, visibility in itertools.product(
            Document.Status.values,
            [Scan.CLEAN, Scan.PENDING, Scan.INFECTED],
            Document.Visibility.values,
        ):
            documents.append(
                make_document(
                    community,
                    owner,
                    status=status,
                    scan_status=scan,
                    visibility=visibility,
                    min_role=Role.EXPERT,
                )
            )
    users = [owner, member, expert, moderator, outsider, functional_admin]
    return users, documents


def test_policy_and_selector_agree(world):
    users, documents = world
    for user in users:
        visible = set(Document.objects.visible_to(user).values_list("pk", flat=True))
        for document in documents:
            document.refresh_from_db()
            expected = policies.can_view_document(user, document)
            assert (document.pk in visible) == expected, (user.email, document.pk)


def test_reader_sees_only_active_clean_documents(world):
    users, _ = world
    member = users[1]
    visible = Document.objects.visible_to(member)
    assert visible.exists()
    assert not visible.exclude(status=Document.Status.ACTIVE).exists()
    assert not visible.exclude(current_version__scan_status=Scan.CLEAN).exists()
    assert not visible.filter(visibility=Document.Visibility.RESTRICTED).exists()


def test_expert_reads_restricted_documents(world):
    users, _ = world
    expert = users[2]
    assert (
        Document.objects.visible_to(expert)
        .filter(visibility=Document.Visibility.RESTRICTED)
        .exists()
    )


def test_owner_sees_pending_and_archived(world):
    users, documents = world
    owner = users[0]
    assert Document.objects.visible_to(owner).count() == len(documents)


def test_anonymous_sees_nothing(world, django_user_model):
    from django.contrib.auth.models import AnonymousUser

    assert not Document.objects.visible_to(AnonymousUser()).exists()


def test_download_needs_role_and_clean_version(world, make_user, add_member):
    users, documents = world
    member, outsider = users[1], users[4]
    open_clean = next(
        d
        for d in documents
        if d.community.access_mode == Community.AccessMode.OPEN
        and d.status == Document.Status.ACTIVE
        and d.current_version.scan_status == Scan.CLEAN
        and d.visibility == Document.Visibility.COMMUNITY
    )
    assert policies.can_view_document(outsider, open_clean)
    assert not policies.can_download(outsider, open_clean)
    assert policies.can_download(member, open_clean)
    open_clean.download_min_role = Role.EXPERT
    assert not policies.can_download(member, open_clean)
    assert policies.can_download(users[3], open_clean)  # moderator manages it
