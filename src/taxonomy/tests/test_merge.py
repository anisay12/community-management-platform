"""Merging two tags (functional administration): links move to the kept tag, without
duplicates, and the merged tag is deleted."""

import pytest
from django.contrib.postgres.search import SearchQuery

from audit.models import AuditEvent
from core.errors import DomainError
from posts.models import Post
from taxonomy import services
from taxonomy.models import Tag

pytestmark = pytest.mark.django_db


@pytest.fixture
def community(make_community):
    return make_community("Python guild")


@pytest.fixture
def author(make_user, community, add_member):
    user = make_user("author@example.com")
    add_member(community, user)
    return user


@pytest.fixture
def make_post():
    def _make(community, author, **extra):
        extra.setdefault("title", "A post")
        extra.setdefault("body", "Body")
        return Post.objects.create(
            community=community, author=author, status=Post.Status.PUBLISHED, **extra
        )

    return _make


@pytest.fixture
def tags():
    return services.get_or_create_tag("Machine learning"), services.get_or_create_tag("ML")


def test_merge_moves_posts_and_communities_without_duplicates(
    functional_admin, make_community, community, author, make_post, tags
):
    target, source = tags
    only_source = make_post(community, author, title="Only source")
    both = make_post(community, author, title="Both")
    untouched = make_post(community, author, title="Untouched")
    only_source.tags.add(source)
    both.tags.add(source, target)
    other = make_community("Data guild")
    community.tags.add(source)
    other.tags.add(source, target)

    result = services.merge_tags(actor=functional_admin, source=source, target=target)

    assert result == target
    assert not Tag.objects.filter(pk=source.pk).exists()
    assert list(only_source.tags.all()) == [target]
    assert list(both.tags.all()) == [target]
    assert list(untouched.tags.all()) == []
    assert list(community.tags.all()) == [target]
    assert list(other.tags.all()) == [target]
    through = Post.tags.through
    assert through.objects.filter(tag=target).count() == 2
    assert set(target.communities.all()) == {community, other}


def test_merge_is_audited(functional_admin, community, author, make_post, tags):
    target, source = tags
    make_post(community, author).tags.add(source)
    community.tags.add(source)
    services.merge_tags(actor=functional_admin, source=source, target=target)
    event = AuditEvent.objects.get(action="tag.merged")
    assert event.actor == functional_admin
    assert event.target_id == str(target.pk)
    assert event.changes["source"] == "ML"
    assert event.changes["posts"] == 1
    assert event.changes["communities"] == 1
    assert event.community_id is None


def test_merge_keeps_target_name_and_key(functional_admin, tags):
    target, source = tags
    services.merge_tags(actor=functional_admin, source=source, target=target)
    target.refresh_from_db()
    assert (target.name, target.key) == ("Machine learning", "machine learning")
    # The merged name can be created again later: its key is free.
    assert services.get_or_create_tag("ml").key == "ml"


def test_merge_refreshes_post_search_vector_and_version(
    functional_admin, community, author, make_post, tags
):
    target, source = tags
    post = make_post(community, author, title="Neural networks", body="Text")
    post.tags.add(source)
    services.merge_tags(actor=functional_admin, source=source, target=target)
    post.refresh_from_db()
    assert post.version == 2
    matches = Post.objects.filter(search_vector=SearchQuery("machine", config="simple"))
    assert list(matches) == [post]


def test_merge_reserved_to_functional_admins(make_user, tags):
    target, source = tags
    with pytest.raises(DomainError) as error:
        services.merge_tags(actor=make_user("member@example.com"), source=source, target=target)
    assert error.value.code == "forbidden"
    assert Tag.objects.count() == 2


def test_merge_refuses_same_tag(functional_admin, tags):
    target, _ = tags
    with pytest.raises(DomainError) as error:
        services.merge_tags(actor=functional_admin, source=target, target=target)
    assert error.value.code == "same_tag"
    assert Tag.objects.filter(pk=target.pk).exists()
