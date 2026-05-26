"""M8: Sichtbarkeit des Subject-Namens als zusaetzliche Achse in
ListRecordAccess.

Vorher: ListRecordAccess hatte attribute (NOT NULL) — die Matrix konnte nur
Attribut-Sichtbarkeit ausdruecken. Der Subject-Name (PERSON.given_name +
family_name) war fuer jeden Listen-Sichter sichtbar, ohne Steuermoeglichkeit.

Jetzt: attribute darf NULL sein. Eine Reihe (record, attribute=NULL,
audience=L) bedeutet "der Subject-Name dieses Records ist fuer Audience L
sichtbar". audience=NULL ist weiterhin 'oeffentlich'.

Backfill: Fuer jeden bestehenden Record wird eine Reihe (record, NULL, NULL)
geschrieben — der Default ist 'Name oeffentlich', also kompatibel zur
bisherigen Render-Erwartung (jede Klassenliste auf Papier hat Namen drauf).
"""
from __future__ import annotations

from django.db import migrations, models


def backfill_default_name_visibility(apps, schema_editor):
    ListRecord = apps.get_model("lists", "ListRecord")
    ListRecordAccess = apps.get_model("lists", "ListRecordAccess")
    rows = []
    for record_id in ListRecord.objects.values_list("pk", flat=True):
        rows.append(
            ListRecordAccess(record_id=record_id, attribute=None, audience=None)
        )
    if rows:
        # ignore_conflicts schuetzt gegen einen zweiten Migrations-Lauf, falls
        # ueber die Schiene 0004 manuell zurueck- und wieder vorgespielt wird.
        ListRecordAccess.objects.bulk_create(rows, ignore_conflicts=True)


def reverse_backfill(apps, schema_editor):
    ListRecordAccess = apps.get_model("lists", "ListRecordAccess")
    ListRecordAccess.objects.filter(attribute__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0003_listrecord_conditional_unique"),
    ]

    operations = [
        migrations.AlterField(
            model_name="listrecordaccess",
            name="attribute",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=models.PROTECT,
                related_name="+",
                to="lists.listattribute",
                verbose_name="Attribut",
                help_text="Leer = Sichtbarkeit des Subject-Namens (M8).",
            ),
        ),
        migrations.RunPython(
            backfill_default_name_visibility,
            reverse_code=reverse_backfill,
        ),
    ]
