# Hand-written — User.last_selected_list: the list a user landed on most
# recently, used to pick the post-login landing list (see CLAUDE.md /
# *Post-login landing*). FK into lists.List, SET_NULL so deleting a list
# never deletes the user.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_passkey_webauthnchallenge_activationtoken"),
        ("lists", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="last_selected_list",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="lists.list",
                verbose_name="zuletzt gewählte Liste",
                help_text=(
                    "Liste, die der Benutzer zuletzt geöffnet hat — steuert, "
                    "auf welcher Listen-Ansicht er nach dem Login landet."
                ),
            ),
        ),
    ]
