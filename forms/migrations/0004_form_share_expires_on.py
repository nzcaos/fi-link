"""Form.share_expires_on — every broadcast link gets an end date.

A form's share link is a registration grant (accounts.views._registration_grant).
Without an end date it was a standing one; forms are about an upcoming event,
so the link now stops working after its last day. Existing links get the
default lifetime (today + 30 days) so nothing breaks on deploy; form admins
adjust it to the event date.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def backfill_expiry(apps, schema_editor):
    Form = apps.get_model("forms", "Form")
    Form.objects.filter(share_token__isnull=False, share_expires_on__isnull=True).update(
        share_expires_on=timezone.localdate() + timedelta(days=30)
    )


class Migration(migrations.Migration):

    dependencies = [
        ("forms", "0003_formpart_body_help_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="form",
            name="share_expires_on",
            field=models.DateField(
                blank=True,
                null=True,
                help_text=(
                    "Letzter Tag, an dem der Freigabe-Link funktioniert (Ansicht, Eintragung, "
                    "Registrierung). Wird beim Erzeugen des Links auf heute + 30 Tage gesetzt — "
                    "in der Regel auf den Tag der Veranstaltung ändern."
                ),
                verbose_name="Link gültig bis",
            ),
        ),
        migrations.RunPython(backfill_expiry, migrations.RunPython.noop),
    ]
