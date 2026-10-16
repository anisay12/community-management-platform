import pytest

from taxonomy.models import Tag

pytestmark = pytest.mark.django_db


@pytest.fixture
def logged(api_client, active_user):
    api_client.force_login(active_user)
    return api_client


def test_prefix_search(logged):
    for name in ["Python", "PyTorch", "Pandas", "Rust"]:
        Tag.objects.create(name=name, slug=name.lower())
    names = [tag["name"] for tag in logged.get("/api/v1/tags/?q=py").json()]
    assert names == ["Python", "PyTorch"]


def test_short_query_returns_nothing(logged):
    Tag.objects.create(name="Python", slug="python")
    assert logged.get("/api/v1/tags/?q=p").json() == []


def test_at_most_eight(logged):
    for index in range(12):
        Tag.objects.create(name=f"tag{index:02d}", slug=f"tag{index:02d}")
    assert len(logged.get("/api/v1/tags/?q=tag").json()) == 8


def test_query_is_case_insensitive(logged):
    for name in ["Python", "PyTorch", "Rust"]:
        Tag.objects.create(name=name, slug=name.lower())
    upper = logged.get("/api/v1/tags/?q=PY").json()
    assert upper == logged.get("/api/v1/tags/?q=py").json()
    assert [tag["name"] for tag in upper] == ["Python", "PyTorch"]


def test_query_ignores_accents(logged):
    Tag.objects.create(name="Éclair", slug="eclair")
    for query in ["ec", "éc"]:
        names = [tag["name"] for tag in logged.get(f"/api/v1/tags/?q={query}").json()]
        assert names == ["Éclair"], query
