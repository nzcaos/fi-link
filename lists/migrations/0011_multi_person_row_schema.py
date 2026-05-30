"""Phase 1 der Mehr-Personen-Zeile (Kind + Eltern in einer Schulklassen-Zeile).

Zwei Schema-Erweiterungen, beide ruekwaerts-kompatibel:

1. ``ListAttribute.applies_to_role`` (``member`` | ``associate``, Default
   ``member``) — markiert, ob ein Attribut zum Mitglied (z. B. Kind) oder zu
   den zugehoerigen Personen (z. B. Eltern) gehoert. Bestehende Attribute
   werden durch den Feld-Default zu ``member`` (kompatibel zur bisherigen
   flachen Anzeige); der Super-Admin schaltet einzelne Attribute spaeter auf
   ``associate``.

2. ``ListRecordAccess.sentinel`` (``name`` | ``email`` | NULL) — disambiguiert
   die beiden Subject-Sentinels (beide mit ``attribute=NULL``). Vor dieser
   Migration war ``attribute=NULL`` eindeutig der Name (M8). Damit der neue
   E-Mail-Sentinel nicht mit dem Namen kollidiert, bekommt **jede bestehende**
   ``attribute IS NULL``-Zeile ``sentinel="name"``. Reale Attribut-Zeilen
   behalten ``sentinel=NULL``.

Keine CheckConstraints/Q-Objekte hier — die Q-Reihenfolge-Falle
(reference-handwritten-migration-q-ordering) ist nicht einschlaegig.
"""
from __future__ import annotations

from django.db import migrations, models


def set_name_sentinel(apps, schema_editor):
    ListRecordAccess = apps.get_model("lists", "ListRecordAccess")
    ListRecordAccess.objects.filter(attribute__isnull=True).update(sentinel="name")


def clear_name_sentinel(apps, schema_editor):
    ListRecordAccess = apps.get_model("lists", "ListRecordAccess")
    ListRecordAccess.objects.filter(attribute__isnull=True).update(sentinel=None)


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0010_phase7a_aggregate_alias"),
    ]

    operations = [
        migrations.AddField(
            model_name="listattribute",
            name="applies_to_role",
            field=models.CharField(
                "gilt für",
                max_length=10,
                choices=[
                    ("member", "Mitglied (z. B. Kind)"),
                    ("associate", "Zugehörige Person (z. B. Elternteil)"),
                ],
                default="member",
                help_text=(
                    "Ob dieses Attribut zum Mitglied selbst (z. B. Kind) oder zu "
                    "den zugehörigen Personen (z. B. Eltern) gehört. 'Zugehörige "
                    "Person' wird pro verknüpfter Person einmal gerendert."
                ),
            ),
        ),
        migrations.AddField(
            model_name="listrecordaccess",
            name="sentinel",
            field=models.CharField(
                "Sentinel",
                max_length=10,
                null=True,
                blank=True,
                choices=[("name", "Name"), ("email", "E-Mail")],
                help_text=(
                    "Nur wenn 'Attribut' leer ist: 'name' = Sichtbarkeit des "
                    "Subject-Namens (M8), 'email' = Sichtbarkeit der Konto-E-Mail."
                ),
            ),
        ),
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
                help_text="Leer = Subject-Sentinel (siehe 'Sentinel').",
            ),
        ),
        migrations.RunPython(set_name_sentinel, reverse_code=clear_name_sentinel),
    ]
