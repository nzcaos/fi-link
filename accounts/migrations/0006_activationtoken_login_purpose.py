# Hand-written — ActivationToken.Purpose.LOGIN: the short-lived mailed login
# link for users who cannot use a passkey (CLAUDE.md / "Login by mail link").

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_activationtoken_pending"),
    ]

    operations = [
        migrations.AlterField(
            model_name="activationtoken",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("activate", "Aktivierung"),
                    ("recover", "Passkey-Wiederherstellung"),
                    ("login", "Anmeldelink"),
                ],
                default="activate",
                max_length=20,
                verbose_name="Zweck",
            ),
        ),
    ]
