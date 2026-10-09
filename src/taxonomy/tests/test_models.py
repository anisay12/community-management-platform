import pytest
from django.db import IntegrityError, transaction

from taxonomy.models import Tag

pytestmark = pytest.mark.django_db


def test_tag_names_are_unique_case_insensitively():
    Tag.objects.create(name="Python", slug="python")
    with pytest.raises(IntegrityError), transaction.atomic():
        Tag.objects.create(name="PYTHON", slug="python-2")


def test_tag_str_is_name():
    assert str(Tag(name="Django", slug="django")) == "Django"
