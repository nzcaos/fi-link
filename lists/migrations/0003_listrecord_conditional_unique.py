"""B1: ListRecord unique constraint conditional auf archived_at IS NULL.

Vorher: unique_together = [("list", "subject")] blockierte Wiedereintritte
nach Self-Removal/Transfer (alte Records sind archiviert, nicht gelöscht).
Jetzt: Unique gilt nur, solange archived_at NULL ist — beliebig viele
archivierte Records pro (list, subject) erlaubt, max. 1 aktiver.
"""
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0002_listinvitetoken"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="listrecord",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="listrecord",
            constraint=models.UniqueConstraint(
                fields=("list", "subject"),
                condition=models.Q(archived_at__isnull=True),
                name="unique_active_record_per_list_subject",
            ),
        ),
    ]
