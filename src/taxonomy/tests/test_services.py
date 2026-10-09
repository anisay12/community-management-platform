import pytest

from taxonomy import services
from taxonomy.models import Tag

pytestmark = pytest.mark.django_db


def test_get_or_create_tag_is_case_insensitive():
    tag = services.get_or_create_tag("Python")
    assert services.get_or_create_tag("python") == tag
    assert Tag.objects.count() == 1


def test_get_or_create_tag_avoids_slug_collisions():
    Tag.objects.create(name="C", slug="c")
    tag = services.get_or_create_tag("C++")
    assert tag.slug.startswith("c-")
    assert services.get_or_create_tag("!!").slug == "tag"


def test_get_or_create_tag_recovers_from_concurrent_slug(monkeypatch):
    Tag.objects.create(name="Other", slug="taken")
    monkeypatch.setattr(services, "_unique_slug", lambda name: "taken")
    tag = services.get_or_create_tag("Race")
    assert tag.name == "Race"
    assert tag.slug.startswith("taken-")
