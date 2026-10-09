import pytest
from django.db.models import F
from django.utils.text import slugify

from communities.models import Community, CommunityCategory, CommunityMembership


@pytest.fixture
def category(db):
    return CommunityCategory.objects.create(name="Test category", slug="test-category")


@pytest.fixture
def make_community(category):
    def _make(name="Python guild", **extra):
        extra.setdefault("tagline", "Everything Python")
        extra.setdefault("category", category)
        extra.setdefault("slug", slugify(name))
        return Community.objects.create(name=name, **extra)

    return _make


@pytest.fixture
def add_member():
    """Add a membership directly, keeping ``Community.member_count`` in sync."""

    def _add(community, user, role=CommunityMembership.Role.MEMBER):
        membership = CommunityMembership.objects.create(community=community, user=user, role=role)
        Community.objects.filter(pk=community.pk).update(member_count=F("member_count") + 1)
        community.member_count += 1
        return membership

    return _add
