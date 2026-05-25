from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


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
