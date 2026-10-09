"""Turn ``AuditEvent.community_id`` (bigint) into a foreign key on the same column.

Step 1 only changes Django's state: the ``community_id`` column is reinterpreted as the
column of a ``community`` foreign key (no constraint, no index yet), so no row is touched.
Step 2 is a plain ``AlterField`` that adds the index and the foreign-key constraint.
No row can reference a missing community: nothing wrote ``community_id`` before L3.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
        ("communities", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="auditevent", name="community_id"),
                migrations.AddField(
                    model_name="auditevent",
                    name="community",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        db_column="community_id",
                        db_constraint=False,
                        db_index=False,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="communities.community",
                        verbose_name="community",
                    ),
                ),
            ],
            database_operations=[],
        ),
        migrations.AlterField(
            model_name="auditevent",
            name="community",
            field=models.ForeignKey(
                blank=True,
                null=True,
                db_column="community_id",
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="communities.community",
                verbose_name="community",
            ),
        ),
    ]
