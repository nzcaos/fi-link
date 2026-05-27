"""Phase 4: OutboundMessage — one row per recipient of a list mail."""
from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models

import lists.models


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0005_listjointoken"),
    ]

    operations = [
        migrations.CreateModel(
            name="OutboundMessage",
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
                    "message_id",
                    models.CharField(
                        help_text="RFC-2822-Wert ohne spitze Klammern.",
                        max_length=255,
                        unique=True,
                        verbose_name="Message-ID",
                    ),
                ),
                (
                    "alias_token",
                    models.CharField(
                        default=lists.models._gen_alias_token,
                        help_text=(
                            "Eindeutig pro Empfänger-Row; speist "
                            "'bounce-<token>@' und 'alias-<token>@'."
                        ),
                        max_length=64,
                        unique=True,
                        verbose_name="Alias-Token",
                    ),
                ),
                (
                    "from_email",
                    models.EmailField(
                        help_text=(
                            "Adresse des Original-Senders (für Bounce- und "
                            "Reply-Routing in Phase 5b)."
                        ),
                        max_length=254,
                        verbose_name="Ursprünglicher Absender",
                    ),
                ),
                (
                    "recipient_email",
                    models.EmailField(max_length=254, verbose_name="Empfänger"),
                ),
                (
                    "subject",
                    models.CharField(blank=True, max_length=998, verbose_name="Betreff"),
                ),
                (
                    "anonymized_from",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "True: From: wurde auf 'alias-<token>@…' "
                            "umgeschrieben (Sender-Adress-Schutz)."
                        ),
                        verbose_name="anonymisierte From-Adresse",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "ausstehend"),
                            ("sent", "versendet"),
                            ("failed", "fehlgeschlagen"),
                            ("bounced", "Bounce"),
                        ],
                        default="pending",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "attempts",
                    models.PositiveSmallIntegerField(
                        default=0, verbose_name="Sende-Versuche"
                    ),
                ),
                (
                    "last_error",
                    models.TextField(blank=True, verbose_name="letzter Fehler"),
                ),
                (
                    "sent_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="versendet am"
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "list",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outbound_messages",
                        to="lists.list",
                        verbose_name="Liste",
                    ),
                ),
            ],
            options={
                "verbose_name": "Versandte Nachricht",
                "verbose_name_plural": "Versandte Nachrichten",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["list", "-created_at"],
                        name="lists_outbound_list_idx",
                    ),
                    models.Index(
                        fields=["status"], name="lists_outbound_status_idx"
                    ),
                ],
            },
        ),
    ]
