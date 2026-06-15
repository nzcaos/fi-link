"""Matrix integration models (docs/matrix-implementation-plan.md, Phase 1).

Kept deliberately decoupled from the accounts/lists/forms domain: a Matrix
account/room is a thin side-record keyed 1:1 to a User / List, so the Matrix
layer can be switched off (MATRIX_ENABLED=False) without touching the core
model. Secrets (the server-assigned password, the service-account access token)
are encrypted at rest with the same Fernet key as LIST_RECORD_VALUE.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django_cryptography.fields import encrypt


class MatrixAccount(models.Model):
    """The Matrix account our server provisioned for a User (Weg A).

    One per User. The password is server-assigned (Weg A onboarding) and shown
    to the parent once for entering it into Element; it is stored encrypted so
    the credentials page can re-display it on request until Weg B (passkey-SSO)
    removes the second secret entirely.
    """

    class Status(models.TextChoices):
        CREATED = "created", "angelegt"
        INVITED = "invited", "in Raum eingeladen"
        ACTIVE = "active", "aktiv"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="matrix_account",
        verbose_name="Benutzer",
    )
    matrix_user_id = models.CharField(
        "Matrix-ID",
        max_length=255,
        unique=True,
        help_text="Vollständige Matrix-ID, z.B. @u-7f3a9c:fichtelink.caos.cloud.",
    )
    password = encrypt(
        models.CharField("Passwort (verschlüsselt)", max_length=255, blank=True)
    )
    onboarding_status = models.CharField(
        "Onboarding-Status",
        max_length=16,
        choices=Status.choices,
        default=Status.CREATED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Matrix-Konto"
        verbose_name_plural = "Matrix-Konten"

    def __str__(self) -> str:
        return self.matrix_user_id


class MatrixRoom(models.Model):
    """The Matrix room mirroring a List (one per matrix-enabled class list)."""

    list = models.OneToOneField(
        "lists.List",
        on_delete=models.CASCADE,
        related_name="matrix_room",
        verbose_name="Liste",
    )
    room_id = models.CharField(
        "Raum-ID",
        max_length=255,
        unique=True,
        help_text="Matrix-Raum-ID, z.B. !abcdef:fichtelink.caos.cloud.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Matrix-Raum"
        verbose_name_plural = "Matrix-Räume"

    def __str__(self) -> str:
        return f"{self.room_id} ({self.list_id})"


class MatrixServiceAccount(models.Model):
    """Singleton: the privileged service account our server acts as.

    Created once by `manage.py matrix_bootstrap_service_account`. Holds the
    access token used for every client-server-API call (create room, invite,
    send, kick, ban). The token is encrypted at rest and never logged (A4).
    Enforced as a singleton by `pk=1` + the bootstrap command's guard.
    """

    matrix_user_id = models.CharField("Service-Matrix-ID", max_length=255, unique=True)
    access_token = encrypt(models.TextField("Access-Token (verschlüsselt)"))
    device_id = models.CharField("Device-ID", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Matrix-Service-Konto"
        verbose_name_plural = "Matrix-Service-Konto"

    def __str__(self) -> str:
        return self.matrix_user_id

    @classmethod
    def get(cls) -> "MatrixServiceAccount | None":
        return cls.objects.first()
