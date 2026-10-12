# Hand-written — ActivationToken.pending_key / pending_token: the invitation a
# registration started from, so the list join survives an activation link
# opened in a different browser than the one that scanned the QR code.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_user_privacy_consent"),
    ]

    operations = [
        migrations.AddField(
            model_name="activationtoken",
            name="pending_key",
            field=models.CharField(blank=True, max_length=40, verbose_name="Einladung (Art)"),
        ),
        migrations.AddField(
            model_name="activationtoken",
            name="pending_token",
            field=models.CharField(blank=True, max_length=64, verbose_name="Einladung (Token)"),
        ),
    ]
