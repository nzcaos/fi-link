"""Phase 5b: MailReleaseToken — a pending forward awaiting a release click."""
from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models

import lists.models


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0007_inboundmessage"),
    ]

    operations = [
        migrations.CreateModel(
            name="MailReleaseToken",
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
                    "token",
                    models.CharField(
                        default=lists.models._gen_invite_token,
                        max_length=64,
                        unique=True,
                        verbose_name="Token",
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("member", "Mitglieder-Freigabe"),
                            ("admin", "Admin-Freigabe"),
                        ],
                        max_length=20,
                        verbose_name="Art",
                    ),
                ),
                (
                    "offer_anonymize",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Nur bei MEMBER-Freigabe echter Mitglieder: der "
                            "Sender darf beim Klick wählen, ob seine Absender-"
                            "Adresse durch einen Alias ersetzt wird."
                        ),
                        verbose_name="Anonymisierung anbieten",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "expires_at",
                    models.DateTimeField(
                        default=lists.models._default_release_expiry,
                        verbose_name="läuft ab",
                    ),
                ),
                (
                    "consumed_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="eingelöst am"
                    ),
                ),
                (
                    "resolution",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("forwarded", "weitergeleitet"),
                            ("rejected", "abgelehnt"),
                        ],
                        max_length=20,
                        verbose_name="Ergebnis",
                    ),
                ),
                (
                    "inbound",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="release_tokens",
                        to="lists.inboundmessage",
                        verbose_name="eingegangene Nachricht",
                    ),
                ),
                (
                    "list",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="release_tokens",
                        to="lists.list",
                        verbose_name="Ziel-Liste",
                    ),
                ),
            ],
            options={
                "verbose_name": "Mail-Freigabe-Token",
                "verbose_name_plural": "Mail-Freigabe-Tokens",
                "ordering": ["-created_at"],
            },
        ),
    ]
