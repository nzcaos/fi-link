"""Phase 3b: ListInviteToken for member-invitation flow."""
from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import lists.models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("lists", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ListInviteToken",
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
                    "target_email",
                    models.EmailField(
                        help_text=(
                            "Adresse, an die die Einladung versendet wird; im "
                            "Neu-USER-Pfad als E-Mail-Prefill verwendet."
                        ),
                        max_length=254,
                        verbose_name="Ziel-E-Mail",
                    ),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("self", "Eigene Person ist Mitglied"),
                            ("via_associate", "Mitglied ist eine andere Person"),
                        ],
                        default="self",
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
                    "invited_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sent_list_invitations",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="eingeladen von",
                    ),
                ),
                (
                    "list",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invitations",
                        to="lists.list",
                        verbose_name="Liste",
                    ),
                ),
                (
                    "target_person",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "Gesetzt: Einladung zielt auf bestehende Person "
                            "(One-Click-Join). Leer: Neu-USER mit Passkey-Enrollment."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="incoming_invitations",
                        to="accounts.person",
                        verbose_name="Ziel-Person",
                    ),
                ),
            ],
            options={
                "verbose_name": "Listen-Einladung",
                "verbose_name_plural": "Listen-Einladungen",
                "ordering": ["-created_at"],
            },
        ),
    ]
