from django.db import migrations

ROLE_CODES = [
    "employee",
    "community_creator",
    "functional_admin",
    "technical_admin",
    "auditor",
]


def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for code in ROLE_CODES:
        Group.objects.get_or_create(name=code)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(create_groups, migrations.RunPython.noop)]
