"""Phase 7a — Aggregate aliases.

Adds the AggregateAlias model (an email address that fans out across lists by
relationship role, e.g. eltern@<domain>) and teaches OutboundMessage /
MailReleaseToken to carry an aggregate source instead of a list. `list` becomes
nullable on both, an `aggregate` FK is added, and a CheckConstraint enforces
that exactly one source is set per row.
"""
from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("lists", "0009_phase6_lifecycle"),
    ]

    operations = [
        migrations.CreateModel(
            name="AggregateAlias",
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
                    "email_alias",
                    models.CharField(
                        help_text="Local-Part der Verteiler-Adresse, z.B. 'eltern' für eltern@<MAIL_DOMAIN>.",
                        max_length=100,
                        unique=True,
                        verbose_name="E-Mail-Alias",
                    ),
                ),
                ("title", models.CharField(max_length=200, verbose_name="Titel")),
                (
                    "included_roles",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text='PersonRelationship-Rollen, z.B. ["Mutter von", "Vater von", "Erziehungsberechtigte von"].',
                        verbose_name="Einbezogene Rollen",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "scope_list",
                    models.ForeignKey(
                        blank=True,
                        help_text=(
                            "Leer = alle Listen. Gesetzt: nur dieser Teilbaum "
                            "(Liste + Unter-Listen). PROTECT: eine als "
                            "Geltungsbereich genutzte Liste lässt sich nicht "
                            "löschen, ohne den Alias vorher umzukonfigurieren — "
                            "sonst würde der Bereich still auf 'alle Listen' "
                            "aufgeweitet."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="aggregate_aliases",
                        to="lists.list",
                        verbose_name="Geltungsbereich (Liste)",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_aggregate_aliases",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="erstellt von",
                    ),
                ),
            ],
            options={
                "verbose_name": "Aggregat-Alias (Verteiler)",
                "verbose_name_plural": "Aggregat-Aliase (Verteiler)",
                "ordering": ["email_alias"],
            },
        ),
        migrations.AlterField(
            model_name="outboundmessage",
            name="list",
            field=models.ForeignKey(
                blank=True,
                help_text="Gesetzt bei Listen-Mail; leer bei Aggregat-Alias-Versand.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="outbound_messages",
                to="lists.list",
                verbose_name="Liste",
            ),
        ),
        migrations.AddField(
            model_name="outboundmessage",
            name="aggregate",
            field=models.ForeignKey(
                blank=True,
                help_text="Gesetzt bei Aggregat-Alias-Versand; leer bei normaler Listen-Mail.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="outbound_messages",
                to="lists.aggregatealias",
                verbose_name="Aggregat-Alias",
            ),
        ),
        migrations.AddConstraint(
            model_name="outboundmessage",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(list__isnull=False, aggregate__isnull=True)
                    | models.Q(list__isnull=True, aggregate__isnull=False)
                ),
                name="outbound_exactly_one_source",
            ),
        ),
        migrations.AlterField(
            model_name="mailreleasetoken",
            name="list",
            field=models.ForeignKey(
                blank=True,
                help_text="Gesetzt bei Listen-Freigabe; leer bei Aggregat-Alias-Freigabe.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="release_tokens",
                to="lists.list",
                verbose_name="Ziel-Liste",
            ),
        ),
        migrations.AddField(
            model_name="mailreleasetoken",
            name="aggregate",
            field=models.ForeignKey(
                blank=True,
                help_text="Gesetzt bei Aggregat-Alias-Freigabe (Super-Admin-Approval / Permitted-Release).",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="release_tokens",
                to="lists.aggregatealias",
                verbose_name="Ziel-Aggregat-Alias",
            ),
        ),
        migrations.AddConstraint(
            model_name="mailreleasetoken",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(list__isnull=False, aggregate__isnull=True)
                    | models.Q(list__isnull=True, aggregate__isnull=False)
                ),
                name="release_token_exactly_one_source",
            ),
        ),
    ]
