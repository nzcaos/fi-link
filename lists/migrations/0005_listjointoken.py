"""Phase 3b-2: ListJoinToken for QR-code multi-use join flow."""
from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import lists.models


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0004_subject_name_visibility"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ListJoinToken",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "expires_at",
                    models.DateTimeField(
                        default=lists.models._default_join_expiry,
                        verbose_name="läuft ab",
                    ),
                ),
                (
                    "revoked_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="widerrufen am"
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_join_tokens",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="erstellt von",
                    ),
                ),
                (
                    "list",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="join_tokens",
                        to="lists.list",
                        verbose_name="Liste",
                    ),
                ),
            ],
            options={
                "verbose_name": "Listen-Beitritts-Token (QR)",
                "verbose_name_plural": "Listen-Beitritts-Tokens (QR)",
                "ordering": ["-created_at"],
            },
        ),
    ]
