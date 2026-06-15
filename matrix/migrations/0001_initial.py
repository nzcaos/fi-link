"""Matrix integration schema: MatrixAccount, MatrixRoom, MatrixServiceAccount.

Handwritten (project convention — no local runtime to run makemigrations).
Encrypted fields use the same django_cryptography.encrypt() wrapper and Fernet
key as lists.ListRecordValue.value.
"""
from __future__ import annotations

import django.db.models.deletion
import django_cryptography.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("lists", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MatrixAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("matrix_user_id", models.CharField(help_text="Vollständige Matrix-ID, z.B. @u-7f3a9c:fichtelink.caos.cloud.", max_length=255, unique=True, verbose_name="Matrix-ID")),
                ("password", django_cryptography.fields.encrypt(models.CharField(blank=True, max_length=255, verbose_name="Passwort (verschlüsselt)"))),
                ("onboarding_status", models.CharField(choices=[("created", "angelegt"), ("invited", "in Raum eingeladen"), ("active", "aktiv")], default="created", max_length=16, verbose_name="Onboarding-Status")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="matrix_account", to=settings.AUTH_USER_MODEL, verbose_name="Benutzer")),
            ],
            options={
                "verbose_name": "Matrix-Konto",
                "verbose_name_plural": "Matrix-Konten",
            },
        ),
        migrations.CreateModel(
            name="MatrixRoom",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("room_id", models.CharField(help_text="Matrix-Raum-ID, z.B. !abcdef:fichtelink.caos.cloud.", max_length=255, unique=True, verbose_name="Raum-ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("list", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="matrix_room", to="lists.list", verbose_name="Liste")),
            ],
            options={
                "verbose_name": "Matrix-Raum",
                "verbose_name_plural": "Matrix-Räume",
            },
        ),
        migrations.CreateModel(
            name="MatrixServiceAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("matrix_user_id", models.CharField(max_length=255, unique=True, verbose_name="Service-Matrix-ID")),
                ("access_token", django_cryptography.fields.encrypt(models.TextField(verbose_name="Access-Token (verschlüsselt)"))),
                ("device_id", models.CharField(blank=True, max_length=255, verbose_name="Device-ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Matrix-Service-Konto",
                "verbose_name_plural": "Matrix-Service-Konto",
            },
        ),
    ]
