# Hand-written — User.privacy_consent_at / privacy_consent_version: proof of
# the user's consent to the privacy notice (/datenschutz/), the legal basis
# for processing (Art. 6 Abs. 1 lit. a DSGVO). Existing users start empty and
# are asked once by PrivacyConsentMiddleware.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_user_last_selected_list"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="privacy_consent_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Einwilligung Datenschutz am",
                help_text="Zeitpunkt, zu dem der Benutzer in die Datenschutzhinweise eingewilligt hat.",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="privacy_consent_version",
            field=models.CharField(
                blank=True,
                max_length=20,
                verbose_name="Version der Datenschutzhinweise",
                help_text=(
                    "Stand der Datenschutzhinweise (settings.PRIVACY_POLICY_VERSION), in den "
                    "eingewilligt wurde. Weicht er vom aktuellen ab, wird erneut gefragt."
                ),
            ),
        ),
    ]
