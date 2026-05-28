"""Phase 7b — composable forms.

FORM / FORM_PART / FORM_PART_ASSET / FORM_ACCESS (filink.md). FormPart.list is a
nullable FK into the lists app (the dynamic list part).
"""
from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("lists", "0010_phase7a_aggregate_alias"),
    ]

    operations = [
        migrations.CreateModel(
            name="Form",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(max_length=200, verbose_name="Titel")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_forms",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="erstellt von",
                    ),
                ),
            ],
            options={
                "verbose_name": "Formular",
                "verbose_name_plural": "Formulare",
                "ordering": ["title"],
            },
        ),
        migrations.CreateModel(
            name="FormPart",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("order", models.PositiveIntegerField(default=0, verbose_name="Reihenfolge")),
                ("title", models.CharField(blank=True, max_length=200, verbose_name="Titel")),
                (
                    "body",
                    models.TextField(
                        blank=True,
                        help_text=(
                            "HTML-Vorlage für diesen Teil. Assets dieses Teils "
                            "per [[asset:<ID>]] einbinden (wird beim Rendern "
                            "durch die Asset-URL ersetzt)."
                        ),
                        verbose_name="HTML-Inhalt",
                    ),
                ),
                (
                    "form",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="parts",
                        to="forms.form",
                        verbose_name="Formular",
                    ),
                ),
                (
                    "list",
                    models.ForeignKey(
                        blank=True,
                        help_text="Optional: bindet diese Liste als dynamischen Teil ein.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="form_parts",
                        to="lists.list",
                        verbose_name="dynamischer Teil (Liste)",
                    ),
                ),
            ],
            options={
                "verbose_name": "Formular-Teil",
                "verbose_name_plural": "Formular-Teile",
                "ordering": ["order", "id"],
            },
        ),
        migrations.CreateModel(
            name="FormPartAsset",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(blank=True, max_length=200, verbose_name="Titel")),
                (
                    "mime_type",
                    models.CharField(
                        default="application/octet-stream",
                        max_length=100,
                        verbose_name="MIME-Typ",
                    ),
                ),
                ("data", models.BinaryField(blank=True, default=b"", verbose_name="Daten")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "part",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assets",
                        to="forms.formpart",
                        verbose_name="Formular-Teil",
                    ),
                ),
            ],
            options={
                "verbose_name": "Formular-Asset",
                "verbose_name_plural": "Formular-Assets",
                "ordering": ["id"],
            },
        ),
        migrations.CreateModel(
            name="FormAccess",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("granted_at", models.DateTimeField(auto_now_add=True)),
                (
                    "form",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="access",
                        to="forms.form",
                        verbose_name="Formular",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="form_access",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Benutzer",
                    ),
                ),
            ],
            options={
                "verbose_name": "Formular-Zugriff",
                "verbose_name_plural": "Formular-Zugriffe",
                "unique_together": {("form", "user")},
            },
        ),
    ]
