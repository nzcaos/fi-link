# Hand-written — ActivationToken.pending_list: the list a registration's
# invitation is for, as a real FK. The invitation tokens are short-lived and
# swept, so the admins' "Nicht abgeschlossene Anmeldungen" page needs the
# association stored on the activation token itself. Backfilled from the
# invitation tokens that still exist.

import django.db.models.deletion
from django.db import migrations, models

_MODELS = {
    "pending_invite_token": "ListInviteToken",
    "pending_join_token": "ListJoinToken",
    "pending_admin_invite_token": "AdminInviteToken",
}


def backfill_pending_list(apps, schema_editor):
    ActivationToken = apps.get_model("accounts", "ActivationToken")
    for key, model_name in _MODELS.items():
        model = apps.get_model("lists", model_name)
        lists_by_token = dict(model.objects.values_list("token", "list_id"))
        for act in ActivationToken.objects.filter(pending_key=key, pending_list__isnull=True):
            list_id = lists_by_token.get(act.pending_token)
            if list_id is not None:
                act.pending_list_id = list_id
                act.save(update_fields=["pending_list"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_activationtoken_login_purpose"),
        ("lists", "0015_fold_own_list_audience_into_public"),
    ]

    operations = [
        migrations.AddField(
            model_name="activationtoken",
            name="pending_list",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pending_activations",
                to="lists.list",
                verbose_name="Einladung (Liste)",
            ),
        ),
        migrations.RunPython(backfill_pending_list, migrations.RunPython.noop),
    ]
