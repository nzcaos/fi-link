# Hand-written for Phase 2 — WebAuthn passkey storage, challenge log,
# and email-link activation tokens.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import accounts.models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Passkey",
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
                    "credential_id",
                    models.BinaryField(
                        help_text="Raw credential ID returned by the authenticator.",
                        unique=True,
                        verbose_name="Credential-ID",
                    ),
                ),
                (
                    "public_key",
                    models.BinaryField(
                        help_text="COSE-encoded public key.",
                        verbose_name="Public Key",
                    ),
                ),
                ("sign_count", models.PositiveBigIntegerField(default=0, verbose_name="Sign Count")),
                (
                    "transports",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text='Hints from the browser, e.g. ["internal", "hybrid"].',
                        verbose_name="Transports",
                    ),
                ),
                (
                    "label",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Vom Benutzer vergebener Name, z.B. 'iPhone' oder 'Arbeit-Laptop'.",
                        max_length=100,
                        verbose_name="Bezeichnung",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="erstellt am")),
                (
                    "last_used_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="zuletzt genutzt am"),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="passkeys",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Benutzer",
                    ),
                ),
            ],
            options={
                "verbose_name": "Passkey",
                "verbose_name_plural": "Passkeys",
                "ordering": ["-last_used_at", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="WebAuthnChallenge",
            fields=[
                (
                    "challenge",
                    models.BinaryField(
                        help_text="Raw random bytes returned to the client.",
                        primary_key=True,
                        serialize=False,
                        verbose_name="Challenge",
                    ),
                ),
                (
                    "purpose",
                    models.CharField(
                        choices=[("register", "Registrierung"), ("login", "Anmeldung")],
                        max_length=20,
                        verbose_name="Zweck",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                (
                    "expected_user",
                    models.ForeignKey(
                        blank=True,
                        help_text="Bei discoverable-credential Login leer; bei add-passkey gesetzt.",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="erwarteter Benutzer",
                    ),
                ),
            ],
            options={
                "verbose_name": "WebAuthn-Challenge",
                "verbose_name_plural": "WebAuthn-Challenges",
            },
        ),
        migrations.AddIndex(
            model_name="webauthnchallenge",
            index=models.Index(fields=["expires_at"], name="accounts_we_expires_idx"),
        ),
        migrations.CreateModel(
            name="ActivationToken",
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
                        default=accounts.models._gen_activation_token,
                        max_length=64,
                        unique=True,
                        verbose_name="Token",
                    ),
                ),
                (
                    "purpose",
                    models.CharField(
                        choices=[
                            ("activate", "Aktivierung"),
                            ("recover", "Passkey-Wiederherstellung"),
                        ],
                        default="activate",
                        max_length=20,
                        verbose_name="Zweck",
                    ),
                ),
                (
                    "email",
                    models.EmailField(
                        help_text="Adresse, an die der Link verschickt wurde — kann vom Person.email abweichen.",
                        max_length=254,
                        verbose_name="Versendet an",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                (
                    "consumed_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="eingelöst am"),
                ),
                (
                    "person",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="activation_tokens",
                        to="accounts.person",
                        verbose_name="Person",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="activation_tokens",
                        to="accounts.user",
                        verbose_name="Benutzer",
                    ),
                ),
            ],
            options={
                "verbose_name": "Aktivierungs-Token",
                "verbose_name_plural": "Aktivierungs-Tokens",
                "ordering": ["-created_at"],
            },
        ),
    ]
