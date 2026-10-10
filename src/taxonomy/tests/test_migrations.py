import importlib
from types import SimpleNamespace

import pytest

migration = importlib.import_module("taxonomy.migrations.0002_tag_key")


class _Tags:
    def __init__(self, names):
        self.rows = [SimpleNamespace(pk=i, name=name, key=None) for i, name in enumerate(names)]
        for row in self.rows:
            row.save = lambda update_fields, row=row: None

    def order_by(self, *fields):
        return self.rows

    def all(self):
        return self.rows


def _apps(tags):
    return SimpleNamespace(get_model=lambda app, model: SimpleNamespace(objects=tags))


def test_backfill_sets_keys():
    tags = _Tags(["Données", "Python"])
    migration.backfill_keys(_apps(tags), None)
    assert [row.key for row in tags.rows] == ["donnees", "python"]


def test_backfill_refuses_colliding_names():
    with pytest.raises(RuntimeError, match="Café, cafe"):
        migration.backfill_keys(_apps(_Tags(["Café", "cafe", "Other"])), None)
