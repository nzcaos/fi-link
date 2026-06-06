"""ListAttribute.display_row: konfigurierbare Zeile fuer die Mitglied-Attribute
im Schulklassen-Mehr-Personen-Layout.

Die Bezugspersonen-Attribute werden bereits als nebeneinander liegende Spalten
gerendert. Die Mitglied-(Kind-)Attribute sollen ebenso nebeneinander stehen,
verteilt auf eine oder mehrere Zeilen. Welche Zeile ein Attribut belegt, wird
hier pro Attribut hinterlegt (Default 1 = erste Zeile, kompatibel zum bisherigen
einzeiligen Verhalten).
"""
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0012_backfill_associate_visibility"),
    ]

    operations = [
        migrations.AddField(
            model_name="listattribute",
            name="display_row",
            field=models.PositiveSmallIntegerField(
                default=1,
                verbose_name="Anzeige-Zeile",
                help_text=(
                    "Nur für die Schulklassen-Ansicht (Mitglied-Attribute): In "
                    "welcher Zeile des Mitglied-Blocks dieses Attribut "
                    "nebeneinander angeordnet wird (1 = erste Zeile, 2 = zweite "
                    "Zeile, …). Innerhalb einer Zeile entscheidet 'Position' "
                    "über die Spaltenreihenfolge."
                ),
            ),
        ),
    ]
