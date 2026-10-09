"""Seed the twelve initial community categories (editable data, not gettext strings)."""

from django.db import migrations
from django.utils.text import slugify

CATEGORIES = [
    ("Data Engineering", "database-gear"),
    ("Data Science et Intelligence Artificielle", "robot"),
    ("Cloud et Architecture", "cloud"),
    ("DevOps et Platform Engineering", "gear-wide-connected"),
    ("Data Governance et Data Quality", "shield-check"),
    ("Business Intelligence et Analytics", "bar-chart-line"),
    ("Cybersécurité", "shield-lock"),
    ("Développement logiciel", "code-slash"),
    ("Agilité et gestion de projet", "kanban"),
    ("Expertise métier", "briefcase"),
    ("Préparation aux certifications", "award"),
    ("Innovation et veille technologique", "lightbulb"),
]


def seed(apps, schema_editor):
    CommunityCategory = apps.get_model("communities", "CommunityCategory")
    for order, (name, icon) in enumerate(CATEGORIES, start=1):
        CommunityCategory.objects.get_or_create(
            slug=slugify(name), defaults={"name": name, "icon": icon, "order": order * 10}
        )


def unseed(apps, schema_editor):
    CommunityCategory = apps.get_model("communities", "CommunityCategory")
    CommunityCategory.objects.filter(
        slug__in=[slugify(name) for name, _ in CATEGORIES], communities__isnull=True
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("communities", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]
