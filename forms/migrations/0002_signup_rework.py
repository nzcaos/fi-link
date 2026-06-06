"""Forms self-service signup rework.

Removes the dynamic-list embedding (FormPart.list) and introduces the
slot/contribution signup model: FormPart gains a ``kind`` discriminator plus
contribution config, Form gains a broadcast ``share_token`` + ``is_open`` flag,
and FormSlot / FormSignup are added. No data backfill — forms has no production
data yet (Phase 8 deployment still open).
"""
from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("forms", "0001_initial"),
    ]

    operations = [
        # --- Form: broadcast link + open/closed flag ---
        migrations.AddField(
            model_name="form",
            name="share_token",
            field=models.CharField(
                blank=True,
                help_text="Token für den Broadcast-Link; wird bei Bedarf erzeugt.",
                max_length=64,
                null=True,
                unique=True,
                verbose_name="Freigabe-Token",
            ),
        ),
        migrations.AddField(
            model_name="form",
            name="is_open",
            field=models.BooleanField(
                default=True,
                help_text="Wenn deaktiviert, ist nur noch Ansicht möglich, keine neuen Einträge.",
                verbose_name="Eintragung offen",
            ),
        ),
        # --- FormPart: drop list FK, add kind + contribution config ---
        migrations.RemoveField(
            model_name="formpart",
            name="list",
        ),
        migrations.AddField(
            model_name="formpart",
            name="kind",
            field=models.CharField(
                choices=[
                    ("html", "HTML-Inhalt"),
                    ("slots", "Aufgaben / Positionen"),
                    ("contributions", "Freitext-Beiträge"),
                ],
                default="html",
                max_length=20,
                verbose_name="Art",
            ),
        ),
        migrations.AddField(
            model_name="formpart",
            name="contribution_label",
            field=models.CharField(
                blank=True,
                default="Mein Beitrag",
                help_text="Nur für Art „Freitext-Beiträge“: Beschriftung des Textfelds.",
                max_length=200,
                verbose_name="Beitrags-Beschriftung",
            ),
        ),
        migrations.AddField(
            model_name="formpart",
            name="contribution_required",
            field=models.BooleanField(
                default=True,
                help_text="Nur für Art „Freitext-Beiträge“: muss der Text ausgefüllt werden?",
                verbose_name="Beitragstext erforderlich",
            ),
        ),
        migrations.AlterField(
            model_name="formpart",
            name="body",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Nur für Art „HTML-Inhalt“: HTML-Vorlage für diesen Teil. "
                    "Assets dieses Teils per [[asset:<ID>]] einbinden (wird beim "
                    "Rendern durch die Asset-URL ersetzt)."
                ),
                verbose_name="HTML-Inhalt",
            ),
        ),
        # --- New models ---
        migrations.CreateModel(
            name="FormSlot",
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
                ("label", models.CharField(max_length=200, verbose_name="Bezeichnung")),
                (
                    "capacity",
                    models.PositiveIntegerField(
                        default=1,
                        help_text="Wie viele Personen können sich für diese Position eintragen?",
                        verbose_name="Plätze",
                    ),
                ),
                (
                    "part",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="slots",
                        to="forms.formpart",
                        verbose_name="Formular-Teil",
                    ),
                ),
            ],
            options={
                "verbose_name": "Position",
                "verbose_name_plural": "Positionen",
                "ordering": ["order", "id"],
            },
        ),
        migrations.CreateModel(
            name="FormSignup",
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
                (
                    "contribution_text",
                    models.CharField(blank=True, max_length=300, verbose_name="Beitrag"),
                ),
                ("name_visible", models.BooleanField(default=True, verbose_name="Name sichtbar")),
                ("email_visible", models.BooleanField(default=False, verbose_name="E-Mail sichtbar")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "part",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="signups",
                        to="forms.formpart",
                        verbose_name="Formular-Teil",
                    ),
                ),
                (
                    "slot",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="signups",
                        to="forms.formslot",
                        verbose_name="Position",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="form_signups",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Benutzer",
                    ),
                ),
            ],
            options={
                "verbose_name": "Eintragung",
                "verbose_name_plural": "Eintragungen",
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="formsignup",
            constraint=models.UniqueConstraint(
                fields=["slot", "user"], name="uniq_signup_per_slot_user"
            ),
        ),
        migrations.AddConstraint(
            model_name="formsignup",
            constraint=models.UniqueConstraint(
                condition=models.Q(slot__isnull=True),
                fields=["part", "user"],
                name="uniq_contribution_per_part_user",
            ),
        ),
    ]
