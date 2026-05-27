"""Phase 5a: InboundMessage — one row per received catch-all mail."""
from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0006_outboundmessage"),
    ]

    operations = [
        migrations.CreateModel(
            name="InboundMessage",
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
                    "imap_uidvalidity",
                    models.BigIntegerField(
                        help_text=(
                            "UIDVALIDITY der Mailbox zum Zeitpunkt des FETCH."
                        ),
                        verbose_name="IMAP UIDVALIDITY",
                    ),
                ),
                (
                    "imap_uid",
                    models.BigIntegerField(
                        help_text=(
                            "Server-vergebene UID der Nachricht — zusammen "
                            "mit UIDVALIDITY eindeutig."
                        ),
                        verbose_name="IMAP UID",
                    ),
                ),
                (
                    "message_id",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "RFC-2822-Wert ohne spitze Klammern; leer wenn "
                            "Header fehlt."
                        ),
                        max_length=255,
                        verbose_name="Message-ID",
                    ),
                ),
                (
                    "from_email",
                    models.CharField(
                        blank=True,
                        help_text="Adress-Teil aus From:; leer bei Parse-Fehler.",
                        max_length=320,
                        verbose_name="Absender",
                    ),
                ),
                (
                    "to_alias",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Local-Part der ersten passenden Empfänger-"
                            "Adresse, lowercase, ohne Plus-Tag."
                        ),
                        max_length=200,
                        verbose_name="Empfänger-Alias (Local-Part)",
                    ),
                ),
                (
                    "to_domain",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Domain-Teil — Phase 5b nutzt das für Multi-"
                            "Domain-Validation."
                        ),
                        max_length=255,
                        verbose_name="Empfänger-Domain",
                    ),
                ),
                (
                    "subject",
                    models.CharField(
                        blank=True, max_length=998, verbose_name="Betreff"
                    ),
                ),
                (
                    "raw_eml",
                    models.BinaryField(
                        help_text=(
                            "Vollständige MIME-Bytes, wie vom IMAP-FETCH "
                            "geliefert."
                        ),
                        verbose_name="RFC-822-Bytes",
                    ),
                ),
                (
                    "received_at",
                    models.DateTimeField(
                        help_text=(
                            "IMAP INTERNALDATE wenn verfügbar, sonst Zeitpunkt "
                            "der FETCH-Antwort."
                        ),
                        verbose_name="empfangen am",
                    ),
                ),
                (
                    "decision",
                    models.CharField(
                        choices=[
                            ("pending", "ausstehend"),
                            ("forwarded", "weitergeleitet"),
                            ("pending_approval", "Freigabe ausstehend"),
                            ("rejected", "abgewiesen"),
                            ("suppressed", "unterdrückt"),
                            ("bounce", "Bounce"),
                            ("unknown_alias", "Alias unbekannt"),
                        ],
                        default="pending",
                        max_length=30,
                        verbose_name="Entscheidung",
                    ),
                ),
                (
                    "reason",
                    models.TextField(
                        blank=True,
                        help_text=(
                            "Phase 5b: Grund für SUPPRESSED/REJECTED/UNKNOWN_ALIAS."
                        ),
                        verbose_name="Begründung",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "matched_list",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "Wenn to_alias auf eine Liste matched — vom "
                            "Consumer gesetzt."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inbound_messages",
                        to="lists.list",
                        verbose_name="Ziel-Liste",
                    ),
                ),
                (
                    "matched_outbound",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "Gesetzt bei Bounce-Match auf 'bounce-<token>@' "
                            "(Phase 5b)."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inbound_correlations",
                        to="lists.outboundmessage",
                        verbose_name="korrelierte ausgehende Nachricht",
                    ),
                ),
            ],
            options={
                "verbose_name": "Eingegangene Nachricht",
                "verbose_name_plural": "Eingegangene Nachrichten",
                "ordering": ["-received_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="inboundmessage",
            constraint=models.UniqueConstraint(
                fields=("imap_uidvalidity", "imap_uid"),
                name="lists_inbound_uid_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="inboundmessage",
            index=models.Index(
                fields=["message_id"], name="lists_inbound_msgid_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="inboundmessage",
            index=models.Index(
                fields=["to_alias"], name="lists_inbound_toalias_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="inboundmessage",
            index=models.Index(
                fields=["decision", "-received_at"],
                name="lists_inbound_decision_idx",
            ),
        ),
    ]
