import unicodedata

from django.db import migrations, models


def _key(name):
    # Frozen copy of ``taxonomy.services.normalize_tag_key`` at the time of this migration.
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.lower().split())[:64]


def backfill_keys(apps, schema_editor):
    Tag = apps.get_model("taxonomy", "Tag")
    by_key = {}
    for tag in Tag.objects.order_by("pk"):
        by_key.setdefault(_key(tag.name), []).append(tag.name)
    collisions = [names for names in by_key.values() if len(names) > 1]
    if collisions:
        listing = "; ".join(", ".join(names) for names in collisions)
        raise RuntimeError(f"Tags normalising to the same key, merge them first: {listing}")
    for tag in Tag.objects.all():
        tag.key = _key(tag.name)
        tag.save(update_fields=["key"])


class Migration(migrations.Migration):
    dependencies = [("taxonomy", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="tag",
            name="key",
            field=models.CharField(editable=False, max_length=64, null=True, verbose_name="key"),
        ),
        migrations.RunPython(backfill_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="tag",
            name="key",
            field=models.CharField(editable=False, max_length=64, unique=True, verbose_name="key"),
        ),
    ]
