"""List.matrix_room_enabled / matrix_broadcast_only (Matrix Phase 3).

Two feature-flag columns on List. `matrix_room_enabled` marks a list as eligible
for a Matrix class room; it backfills to True for existing school-class lists —
identified by a set `cohort_grade` (the data-driven "this is a class" signal,
CLAUDE.md / school-class lifecycle). This only marks eligibility; rooms are
created explicitly by `manage.py matrix_create_rooms`, never by this migration.

Handwritten (project convention — no local runtime to run makemigrations).
"""
from __future__ import annotations

from django.db import migrations, models


def enable_for_cohort_lists(apps, schema_editor):
    List = apps.get_model("lists", "List")
    List.objects.filter(cohort_grade__isnull=False).update(matrix_room_enabled=True)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0013_listattribute_display_row"),
    ]

    operations = [
        migrations.AddField(
            model_name="list",
            name="matrix_room_enabled",
            field=models.BooleanField(
                default=False,
                verbose_name="Matrix-Klassenraum aktiv",
                help_text="Wenn aktiv, bekommt diese Liste einen Matrix-Klassenraum (nur Schulklassen).",
            ),
        ),
        migrations.AddField(
            model_name="list",
            name="matrix_broadcast_only",
            field=models.BooleanField(
                default=False,
                verbose_name="Matrix-Broadcast-Modus",
                help_text="Wenn aktiv, dürfen nur Admins (Elternvertretung) im Raum senden.",
            ),
        ),
        migrations.RunPython(enable_for_cohort_lists, noop_reverse),
    ]
