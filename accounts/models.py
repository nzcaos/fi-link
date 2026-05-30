from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


def _gen_activation_token() -> str:
    return secrets.token_urlsafe(32)


class Person(models.Model):
    """A real-world person. May or may not have a login (User).

    Children in a school-class list are Persons without Users. Their parents are
    separate Persons that DO have Users and are linked via PersonRelationship.

    Email is intentionally NOT unique: two parents sharing a family mailbox each
    register as separate Users under separate Persons whose `email` happens to
    coincide. See CLAUDE.md / "PERSON vs. USER".
    """

    given_name = models.CharField("Vorname", max_length=200)
    family_name = models.CharField("Nachname", max_length=200)
    email = models.EmailField("E-Mail", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Person"
        verbose_name_plural = "Personen"
        ordering = ["family_name", "given_name"]

    def __str__(self) -> str:
        return f"{self.given_name} {self.family_name}".strip()

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.family_name}".strip()


class UserManager(BaseUserManager):
    def _create_user(self, *, person: Person, password: str | None, **extra) -> "User":
        user = self.model(person=person, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, *, person: Person, password: str | None = None, **extra) -> "User":
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(person=person, password=password, **extra)

    def create_superuser(self, username: str, password: str | None = None, **extra) -> "User":
        """Bridge `manage.py createsuperuser` to our Person/User split.

        Phase 1 convenience: prompts for username + password only. A placeholder
        Person is created which the super-admin can edit via the admin UI. In
        Phase 2 the real bootstrap path is `manage.py bootstrap_super_admin`.
        """
        person = Person.objects.create(given_name="Super", family_name="Admin")
        extra["is_staff"] = True
        extra["is_superuser"] = True
        extra["is_active"] = True
        return self._create_user(
            person=person, password=password, username=username, **extra
        )


class User(AbstractBaseUser, PermissionsMixin):
    """A Person with login credentials.

    USERNAME_FIELD is an internal `username` (defaults to None unless set by
    `createsuperuser` or — from Phase 2 onward — by the passkey-enrollment
    flow). Login itself happens via WebAuthn discoverable credentials; the
    username is only used by Django's admin login and as a stable handle.
    """

    person = models.OneToOneField(
        Person,
        on_delete=models.PROTECT,
        related_name="user",
        verbose_name="Person",
    )
    username = models.CharField(
        "Benutzername",
        max_length=150,
        unique=True,
        help_text="Interner Identifier. Login passiert ab Phase 2 per Passkey.",
    )
    is_active = models.BooleanField("aktiv", default=True)
    is_staff = models.BooleanField(
        "Admin-Zugang",
        default=False,
        help_text="Darf sich am Django-Admin anmelden.",
    )
    date_joined = models.DateTimeField("registriert am", auto_now_add=True)
    last_selected_list = models.ForeignKey(
        "lists.List",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        verbose_name="zuletzt gewählte Liste",
        help_text=(
            "Liste, die der Benutzer zuletzt geöffnet hat — steuert, auf "
            "welcher Listen-Ansicht er nach dem Login landet."
        ),
    )

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = "Benutzer"
        verbose_name_plural = "Benutzer"
        ordering = ["username"]

    def __str__(self) -> str:
        return f"{self.person} ({self.username})"

    @property
    def email(self) -> str | None:
        return self.person.email if self.person_id else None

    def get_full_name(self) -> str:
        return self.person.full_name

    def get_short_name(self) -> str:
        return self.person.given_name


# ---------------------------------------------------------------------------
# WebAuthn / Passkeys (Phase 2)
# ---------------------------------------------------------------------------


class Passkey(models.Model):
    """A registered WebAuthn credential bound to a User.

    Multiple Passkeys per User are encouraged — see CLAUDE.md /
    "Registration and login flow". Stored fields are exactly what
    `py_webauthn` needs for an authentication ceremony.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="passkeys",
        verbose_name="Benutzer",
    )
    credential_id = models.BinaryField(
        "Credential-ID",
        unique=True,
        help_text="Raw credential ID returned by the authenticator.",
    )
    public_key = models.BinaryField(
        "Public Key",
        help_text="COSE-encoded public key.",
    )
    sign_count = models.PositiveBigIntegerField("Sign Count", default=0)
    transports = models.JSONField(
        "Transports",
        default=list,
        blank=True,
        help_text='Hints from the browser, e.g. ["internal", "hybrid"].',
    )
    label = models.CharField(
        "Bezeichnung",
        max_length=100,
        default="",
        blank=True,
        help_text="Vom Benutzer vergebener Name, z.B. 'iPhone' oder 'Arbeit-Laptop'.",
    )
    created_at = models.DateTimeField("erstellt am", auto_now_add=True)
    last_used_at = models.DateTimeField("zuletzt genutzt am", null=True, blank=True)

    class Meta:
        verbose_name = "Passkey"
        verbose_name_plural = "Passkeys"
        ordering = ["-last_used_at", "-created_at"]

    def __str__(self) -> str:
        return f"{self.label or 'Passkey'} ({self.user})"


class WebAuthnChallenge(models.Model):
    """A pending challenge issued by register-begin or login-begin, redeemed
    at finish-time. Stored in Postgres rather than process memory so a worker
    restart doesn't drop the in-flight ceremony — see CLAUDE.md /
    "Challenge storage".
    """

    class Purpose(models.TextChoices):
        REGISTER = "register", "Registrierung"
        LOGIN = "login", "Anmeldung"

    challenge = models.BinaryField(
        "Challenge",
        primary_key=True,
        help_text="Raw random bytes returned to the client.",
    )
    purpose = models.CharField(
        "Zweck",
        max_length=20,
        choices=Purpose.choices,
    )
    expected_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name="erwarteter Benutzer",
        help_text="Bei discoverable-credential Login leer; bei add-passkey gesetzt.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = "WebAuthn-Challenge"
        verbose_name_plural = "WebAuthn-Challenges"
        indexes = [models.Index(fields=["expires_at"], name="accounts_we_expires_idx")]

    def __str__(self) -> str:
        return f"{self.purpose} challenge expires {self.expires_at:%Y-%m-%d %H:%M:%S}"

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at


class ActivationToken(models.Model):
    """One-shot email-bound link that grants the right to enroll a passkey.

    Used for:
      - new-user registration (`person` set, `user` null until consumed)
      - invitation activation (`person`/`user` pre-created by inviter)
      - admin-triggered recovery (`reset_passkeys`)
    A token alone never confers session access — clicking only opens the
    enrollment page. See CLAUDE.md / "Account recovery" and "Admin handover".
    """

    class Purpose(models.TextChoices):
        ACTIVATE = "activate", "Aktivierung"
        RECOVER = "recover", "Passkey-Wiederherstellung"

    token = models.CharField(
        "Token",
        max_length=64,
        unique=True,
        default=_gen_activation_token,
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.CASCADE,
        related_name="activation_tokens",
        verbose_name="Person",
    )
    user = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="activation_tokens",
        verbose_name="Benutzer",
    )
    purpose = models.CharField(
        "Zweck",
        max_length=20,
        choices=Purpose.choices,
        default=Purpose.ACTIVATE,
    )
    email = models.EmailField(
        "Versendet an",
        help_text="Adresse, an die der Link verschickt wurde — kann vom Person.email abweichen.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField("eingelöst am", null=True, blank=True)

    class Meta:
        verbose_name = "Aktivierungs-Token"
        verbose_name_plural = "Aktivierungs-Tokens"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.purpose} {self.email} ({'eingelöst' if self.consumed_at else 'offen'})"

    def is_valid(self) -> bool:
        return self.consumed_at is None and timezone.now() < self.expires_at

    @classmethod
    def issue(
        cls,
        *,
        person: Person,
        email: str,
        purpose: str = Purpose.ACTIVATE,
        ttl: timedelta = timedelta(days=7),
        user: "User | None" = None,
    ) -> "ActivationToken":
        return cls.objects.create(
            person=person,
            user=user,
            email=email,
            purpose=purpose,
            expires_at=timezone.now() + ttl,
        )
