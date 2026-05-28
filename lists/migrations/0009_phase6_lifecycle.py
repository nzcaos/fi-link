"""Phase 6: School-class lifecycle — PendingTransfer (class transfer with
destination-admin consent) and AdminInviteToken (admin handover/add)."""
from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import lists.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0002_passkey_webauthnchallenge_activationtoken"),
        ("lists", "0008_mailreleasetoken"),
    ]

    operations = [
        migrations.AlterField(
            model_name="list",
            name="cohort_grade",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                help_text=(
                    "Klassenstufe der Lettern-Klassen (5–10 G8, 5–11 G9). "
                    "Kursstufe wird über cohort_track=leer markiert; K1/K2 sind "
                    "die Stufen oberhalb der letzten Lettern-Klasse (G8: 11/12, "
                    "G9: 12/13). Nur Schulklassen-Vorlagen."
                ),
                verbose_name="Klassenstufe",
            ),
        ),
        migrations.CreateModel(
            name="PendingTransfer",
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
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "ausstehend"),
                            ("accepted", "angenommen"),
                            ("rejected", "abgelehnt"),
                        ],
                        default="pending",
                        max_length=20,
                        verbose_name="Status",
                    ),
                ),
                (
                    "resolved_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="entschieden am"
                    ),
                ),
                (
                    "from_list",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outgoing_transfers",
                        to="lists.list",
                        verbose_name="von Liste",
                    ),
                ),
                (
                    "to_list",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="incoming_transfers",
                        to="lists.list",
                        verbose_name="nach Liste",
                    ),
                ),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pending_transfers",
                        to="accounts.person",
                        verbose_name="Person",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="requested_transfers",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="beantragt von",
                    ),
                ),
                (
                    "resolved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="resolved_transfers",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="entschieden von",
                    ),
                ),
            ],
            options={
                "verbose_name": "Klassenwechsel-Antrag",
                "verbose_name_plural": "Klassenwechsel-Anträge",
                "ordering": ["-requested_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="pendingtransfer",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("person", "from_list", "to_list"),
                name="unique_pending_transfer_per_person_route",
            ),
        ),
        migrations.CreateModel(
            name="AdminInviteToken",
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
                    "to_email",
                    models.EmailField(
                        help_text="Adresse, an die die Admin-Einladung versendet wird.",
                        max_length=254,
                        verbose_name="Ziel-E-Mail",
                    ),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("handover", "Übergabe (ich gebe ab)"),
                            ("add", "Hinzufügen (zusätzlicher Admin)"),
                        ],
                        default="add",
                        max_length=20,
                        verbose_name="Modus",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "expires_at",
                    models.DateTimeField(
                        default=lists.models._default_invite_expiry,
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
                    "list",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="admin_invitations",
                        to="lists.list",
                        verbose_name="Liste",
                    ),
                ),
                (
                    "from_user",
                    models.ForeignKey(
                        blank=True,
                        help_text="Leer, wenn von einem Super-Admin angestoßen.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sent_admin_invitations",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="übergebender Admin",
                    ),
                ),
            ],
            options={
                "verbose_name": "Admin-Einladung",
                "verbose_name_plural": "Admin-Einladungen",
                "ordering": ["-created_at"],
            },
        ),
    ]
