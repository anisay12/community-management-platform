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


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Python", "python"),
        ("Données", "donnees"),
        ("  Machine   Learning ", "machine learning"),
        ("ÉLÉGANCE", "elegance"),
    ],
)
def test_normalize_tag_key(name, key):
    assert services.normalize_tag_key(name) == key


def test_get_or_create_tag_ignores_accents():
    tag = services.get_or_create_tag("Données")
    assert tag.key == "donnees"
    assert services.get_or_create_tag("donnees") == tag
    assert Tag.objects.count() == 1


def test_saving_a_tag_sets_its_key():
    tag = Tag.objects.create(name="Café", slug="cafe")
    assert tag.key == "cafe"
