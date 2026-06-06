"""Body is now a description shown for every part kind (above the signup list),
not just for HTML parts — update its help_text accordingly."""
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forms", "0002_signup_rework"),
    ]

    operations = [
        migrations.AlterField(
            model_name="formpart",
            name="body",
            field=models.TextField(
                blank=True,
                help_text=(
                    "HTML-Beschreibung dieses Teils. Bei Helfer- und Beitrags-Teilen "
                    "wird sie über der Eintragungs-Liste angezeigt. Assets dieses Teils "
                    "per [[asset:<ID>]] einbinden (wird beim Rendern durch die Asset-URL "
                    "ersetzt)."
                ),
                verbose_name="HTML-Inhalt",
            ),
        ),
    ]
