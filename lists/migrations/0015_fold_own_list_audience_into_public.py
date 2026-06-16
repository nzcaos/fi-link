"""Fold "own-list" visibility audiences into public (NULL).

The visibility matrix no longer offers the record's OWN list as an audience
option: "members of the own list" is identical to "public" (everyone who can
see the list). Pre-existing LIST_RECORD_ACCESS rows whose `audience` IS the
record's own list are therefore converted to `audience = NULL` (public). Where
a public row already exists for the same (record, attribute, sentinel), the
redundant own-list row is deleted instead, to avoid a duplicate.

Handwritten (project convention). One-way data fix; reverse is a no-op.
"""
from __future__ import annotations

from django.db import migrations


def own_list_audience_to_public(apps, schema_editor):
    LRA = apps.get_model("lists", "ListRecordAccess")
    rows = LRA.objects.filter(audience__isnull=False).values(
        "id", "audience_id", "record_id", "record__list_id", "attribute_id", "sentinel"
    )
    for r in rows:
        if r["audience_id"] != r["record__list_id"]:
            continue  # a real cross-list grant (parent/child) — leave untouched
        public_exists = LRA.objects.filter(
            record_id=r["record_id"],
            attribute_id=r["attribute_id"],
            sentinel=r["sentinel"],
            audience__isnull=True,
        ).exists()
        if public_exists:
            LRA.objects.filter(id=r["id"]).delete()
        else:
            LRA.objects.filter(id=r["id"]).update(audience=None)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0014_list_matrix_fields"),
    ]

    operations = [
        migrations.RunPython(own_list_audience_to_public, noop_reverse),
    ]
