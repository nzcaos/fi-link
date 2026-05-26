"""Domain models for lists, records, visibility, hierarchy and send permissions.

Mirrors the entities in CLAUDE.md ("Core domain model" + "Architecture decisions
(domain model)"). Phase 1 only defines the schema; behavior (visibility service,
mail pipeline, lifecycle ops, etc.) lands in later phases.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django_cryptography.fields import encrypt


def _gen_invite_token() -> str:
    return secrets.token_urlsafe(32)


def _default_invite_expiry():
    return timezone.now() + timedelta(days=30)


# ---------------------------------------------------------------------------
# Templates and attributes
# ---------------------------------------------------------------------------


class ListTemplate(models.Model):
    """Defines the shape and behavior of all Lists derived from it.

    Templates are super-admin only (see CLAUDE.md / "Super-Admin scope").
    """

    class MemberSubjectMode(models.TextChoices):
        SELF = "self", "Eigene Person ist Mitglied"
        VIA_ASSOCIATE = "via_associate", "Mitglied ist eine andere Person"

    name = models.CharField("Name", max_length=200, unique=True)
    description = models.TextField("Beschreibung", blank=True)
    member_subject_mode = models.CharField(
        "Mitglieder-Modus",
        max_length=20,
        choices=MemberSubjectMode.choices,
        default=MemberSubjectMode.SELF,
        help_text=(
            "self: Wer sich registriert, IST das Mitglied (VHS-Kurs, Förderverein). "
            "via_associate: Wer sich registriert, meldet jemand anderen an (Schulklasse)."
        ),
    )
    relationship_roles = models.JSONField(
        "Beziehungsrollen",
        default=list,
        blank=True,
        help_text='Liste von Rollen für PersonRelationship, z.B. ["Mutter von", "Vater von", "Erziehungsberechtigte von"].',
    )
    default_record_role = models.CharField(
        "Default-Rolle für neue Einträge",
        max_length=20,
        choices=[("member", "Mitglied"), ("associate", "Zugeordnet")],
        default="member",
    )
    display_default = models.JSONField(
        "Default-Anzeige",
        default=dict,
        blank=True,
        help_text="Welche Attribute werden per Default in der Listenansicht angezeigt (Phase 3a).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Listen-Vorlage"
        verbose_name_plural = "Listen-Vorlagen"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class ListAttribute(models.Model):
    """A single field on a ListTemplate.

    `must_be_public` overrides per-record visibility settings.
    """

    class Type(models.TextChoices):
        TEXT = "text", "Text"
        EMAIL = "email", "E-Mail"
        PHONE = "phone", "Telefon"
        NUMBER = "number", "Zahl"
        CHOICE = "choice", "Auswahl"
        CHECKBOX = "checkbox", "Häkchen"
        USER_RELATIONSHIP = "user_relationship", "Personen-Beziehung"

    template = models.ForeignKey(
        ListTemplate,
        on_delete=models.CASCADE,
        related_name="attributes",
        verbose_name="Vorlage",
    )
    name = models.CharField("Name", max_length=200)
    type = models.CharField("Typ", max_length=30, choices=Type.choices)
    must_be_public = models.BooleanField(
        "muss öffentlich sein",
        default=False,
        help_text="Wenn gesetzt, kann der Eigentümer dieses Feld nicht verbergen.",
    )
    choices = models.JSONField(
        "Auswahl-Werte",
        default=list,
        blank=True,
        help_text="Nur für Typ 'Auswahl': Liste der wählbaren Werte.",
    )
    choice_cap = models.PositiveIntegerField(
        "Auswahl-Kapazität",
        null=True,
        blank=True,
        help_text="Nur für Typ 'Auswahl': Maximale Belegung pro Wert.",
    )
    position = models.PositiveIntegerField("Position", default=0)

    class Meta:
        verbose_name = "Listen-Attribut"
        verbose_name_plural = "Listen-Attribute"
        ordering = ["position", "id"]
        unique_together = [("template", "name")]

    def __str__(self) -> str:
        return f"{self.template.name} · {self.name}"


# ---------------------------------------------------------------------------
# Lists, hierarchy, cohort metadata
# ---------------------------------------------------------------------------


class List(models.Model):
    """A concrete mailing/contact list.

    Hierarchy via `parent` (CLAUDE.md / "List hierarchy"). Cohort fields are
    only meaningful for school-class templates and are null otherwise.
    """

    class Visibility(models.TextChoices):
        PUBLIC_VISIBLE = "public_visible", "öffentlich sichtbar"
        PUBLIC_EDITABLE = "public_editable", "öffentlich bearbeitbar"
        PRIVATE = "private", "privat"

    class CurriculumTrack(models.TextChoices):
        G8 = "G8", "G8"
        G9 = "G9", "G9"

    title = models.CharField("Titel", max_length=200)
    email_alias = models.CharField(
        "E-Mail-Alias",
        max_length=100,
        unique=True,
        help_text="Local-Part der Listen-Adresse, z.B. '5a' für 5a@<MAIL_DOMAIN>.",
    )
    template = models.ForeignKey(
        ListTemplate,
        on_delete=models.PROTECT,
        related_name="lists",
        verbose_name="Vorlage",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
        verbose_name="übergeordnete Liste",
    )
    visibility = models.CharField(
        "Sichtbarkeit",
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )

    # Cohort metadata — only meaningful for school-class templates.
    cohort_grade = models.PositiveSmallIntegerField(
        "Klassenstufe",
        null=True,
        blank=True,
        help_text="5–12. Nur für Schulklassen-Vorlagen.",
    )
    cohort_track = models.CharField(
        "Klassen-Buchstabe",
        max_length=4,
        null=True,
        blank=True,
        help_text="a, b, c, d, … oder leer für Kursstufe.",
    )
    curriculum_track = models.CharField(
        "Schulzweig",
        max_length=2,
        choices=CurriculumTrack.choices,
        null=True,
        blank=True,
    )

    archived_at = models.DateTimeField("archiviert am", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Liste"
        verbose_name_plural = "Listen"
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title


# ---------------------------------------------------------------------------
# Membership and admin rights (USER-based, see CLAUDE.md / PERSON vs. USER)
# ---------------------------------------------------------------------------


class ListAdmin(models.Model):
    """Admins of a list — must be Users (have a login)."""

    list = models.ForeignKey(
        List,
        on_delete=models.CASCADE,
        related_name="admins",
        verbose_name="Liste",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="admin_of_lists",
        verbose_name="Benutzer",
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Listen-Admin"
        verbose_name_plural = "Listen-Admins"
        unique_together = [("list", "user")]

    def __str__(self) -> str:
        return f"{self.user} admin of {self.list}"


class ListAccess(models.Model):
    """Benutzergruppe: Users who can see the list. PERSONs without USERs are
    never in here (and therefore never an audience for visibility rules).
    """

    list = models.ForeignKey(
        List,
        on_delete=models.CASCADE,
        related_name="members",
        verbose_name="Liste",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="member_of_lists",
        verbose_name="Benutzer",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Benutzergruppen-Mitgliedschaft"
        verbose_name_plural = "Benutzergruppe (Mitgliedschaften)"
        unique_together = [("list", "user")]

    def __str__(self) -> str:
        return f"{self.user} in {self.list}"


# ---------------------------------------------------------------------------
# Records, values, visibility
# ---------------------------------------------------------------------------


class ListRecord(models.Model):
    """One record per Person per List (each user has at most one entry per
    list). The subject is a Person — which may or may not also be a User.
    """

    class Role(models.TextChoices):
        MEMBER = "member", "Mitglied"
        ASSOCIATE = "associate", "Zugeordnet"

    list = models.ForeignKey(
        List,
        on_delete=models.CASCADE,
        related_name="records",
        verbose_name="Liste",
    )
    subject = models.ForeignKey(
        "accounts.Person",
        on_delete=models.PROTECT,
        related_name="records",
        verbose_name="Person",
    )
    role = models.CharField(
        "Rolle",
        max_length=20,
        choices=Role.choices,
        default=Role.MEMBER,
        help_text="member: echtes Mitglied der Liste. associate: zugeordnet (z.B. Elternteil eines Kindes).",
    )
    archived_at = models.DateTimeField("archiviert am", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Eintrag"
        verbose_name_plural = "Einträge"
        constraints = [
            models.UniqueConstraint(
                fields=["list", "subject"],
                condition=models.Q(archived_at__isnull=True),
                name="unique_active_record_per_list_subject",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.subject} in {self.list}"


class ListRecordValue(models.Model):
    """The actual value of one attribute on one record. Encrypted at rest with
    Fernet via django-cryptography (single installation-wide key)."""

    record = models.ForeignKey(
        ListRecord,
        on_delete=models.CASCADE,
        related_name="values",
        verbose_name="Eintrag",
    )
    attribute = models.ForeignKey(
        ListAttribute,
        on_delete=models.PROTECT,
        related_name="values",
        verbose_name="Attribut",
    )
    value = encrypt(models.TextField("Wert (verschlüsselt)", blank=True))

    class Meta:
        verbose_name = "Eintrags-Wert"
        verbose_name_plural = "Eintrags-Werte"
        unique_together = [("record", "attribute")]

    def __str__(self) -> str:
        return f"{self.record} · {self.attribute.name}"


class ListRecordAccess(models.Model):
    """Per-(record, attribute, audience) visibility row.

    The audience is a List_ID — "show this field to members of list X". NULL
    audience maps the spec's '0' (= public).
    """

    record = models.ForeignKey(
        ListRecord,
        on_delete=models.CASCADE,
        related_name="access",
        verbose_name="Eintrag",
    )
    attribute = models.ForeignKey(
        ListAttribute,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="Attribut",
    )
    audience = models.ForeignKey(
        List,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name="Sichtbar für (Liste)",
        help_text="Leer = öffentlich.",
    )

    class Meta:
        verbose_name = "Eintrags-Sichtbarkeit"
        verbose_name_plural = "Eintrags-Sichtbarkeiten"
        unique_together = [("record", "attribute", "audience")]

    def __str__(self) -> str:
        audience = self.audience.title if self.audience_id else "öffentlich"
        return f"{self.record} · {self.attribute.name} → {audience}"


# ---------------------------------------------------------------------------
# Family / triade model
# ---------------------------------------------------------------------------


class PersonRelationship(models.Model):
    """Captures the connection between a member (`subject`) and an associate
    (`related`). E.g. subject=Kind, related=Mutter, role="Mutter von".

    The role string is drawn from the LISTTEMPLATE.relationship_roles taxonomy
    of the LIST in which the relationship is used; not enforced at the schema
    level because a Person can have relationships across multiple list contexts.
    """

    subject_person = models.ForeignKey(
        "accounts.Person",
        on_delete=models.CASCADE,
        related_name="relationships_as_subject",
        verbose_name="Person (Mitglied)",
    )
    related_person = models.ForeignKey(
        "accounts.Person",
        on_delete=models.CASCADE,
        related_name="relationships_as_related",
        verbose_name="Person (Zugeordnet)",
    )
    role = models.CharField(
        "Rolle",
        max_length=100,
        help_text='Z.B. "Mutter von", "Vater von", "Erziehungsberechtigte von".',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Personen-Beziehung"
        verbose_name_plural = "Personen-Beziehungen"
        unique_together = [("subject_person", "related_person", "role")]

    def __str__(self) -> str:
        return f"{self.related_person} {self.role} {self.subject_person}"


class RecordManager(models.Model):
    """Who has edit rights on a given record. Replaces the spec's "only creator
    edits" with a richer model: a child's record may have multiple managers
    (both parents), all marked `basis=guardian`.
    """

    class Basis(models.TextChoices):
        CREATOR = "creator", "Ersteller"
        INVITED = "invited", "eingeladen"
        GUARDIAN = "guardian", "Erziehungsberechtigte"
        SELF_REGISTERED = "self_registered", "selbst registriert"

    record = models.ForeignKey(
        ListRecord,
        on_delete=models.CASCADE,
        related_name="managers",
        verbose_name="Eintrag",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="managed_records",
        verbose_name="Benutzer",
    )
    basis = models.CharField(
        "Grundlage",
        max_length=20,
        choices=Basis.choices,
    )
    since = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Eintrags-Verwalter"
        verbose_name_plural = "Eintrags-Verwalter"
        unique_together = [("record", "user")]

    def __str__(self) -> str:
        return f"{self.user} manages {self.record} ({self.basis})"


# ---------------------------------------------------------------------------
# Cross-list send permissions
# ---------------------------------------------------------------------------


class ListSendPermission(models.Model):
    """Members of `granted_to_list` may send mail to `target_list`'s address.

    Implicit defaults from `List.parent` are NOT stored here — they are
    resolved at decision time. This table holds only explicit grants and
    explicit overrides of the implicit defaults.
    """

    target_list = models.ForeignKey(
        List,
        on_delete=models.CASCADE,
        related_name="received_send_permissions",
        verbose_name="Ziel-Liste",
    )
    granted_to_list = models.ForeignKey(
        List,
        on_delete=models.CASCADE,
        related_name="granted_send_permissions",
        verbose_name="berechtigte Liste",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="granted_send_permissions",
        verbose_name="erteilt von",
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    requires_release_click = models.BooleanField(
        "Bestätigungs-Klick nötig",
        default=False,
    )
    transitive = models.BooleanField(
        "transitiv (auf Unter-Listen)",
        default=False,
        help_text="Wenn true, gilt die Berechtigung auch für Unter-Listen der Ziel-Liste.",
    )

    class Meta:
        verbose_name = "Listen-Senderecht"
        verbose_name_plural = "Listen-Senderechte"
        unique_together = [("target_list", "granted_to_list")]

    def __str__(self) -> str:
        return f"{self.granted_to_list} → {self.target_list}"


# ---------------------------------------------------------------------------
# Invitations (CLAUDE.md / "Member invitation and LIST_INVITE_TOKEN")
# ---------------------------------------------------------------------------


class ListInviteToken(models.Model):
    """A one-shot invitation to join a list.

    Two click-time branches keyed by `target_person`:
    - NULL: recipient is not yet a User. Click triggers passkey enrollment +
      User/Person activation + onboarding wizard.
    - set: recipient is an existing User. Click requires Passkey login as the
      User linked to `target_person`, then drops them straight into the
      record-edit form (no enrollment, no re-capture of personal data).
    """

    class Mode(models.TextChoices):
        SELF = "self", "Eigene Person ist Mitglied"
        VIA_ASSOCIATE = "via_associate", "Mitglied ist eine andere Person"

    token = models.CharField(
        "Token",
        max_length=64,
        unique=True,
        default=_gen_invite_token,
    )
    list = models.ForeignKey(
        List,
        on_delete=models.CASCADE,
        related_name="invitations",
        verbose_name="Liste",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sent_list_invitations",
        verbose_name="eingeladen von",
    )
    target_email = models.EmailField(
        "Ziel-E-Mail",
        help_text="Adresse, an die die Einladung versendet wird; im Neu-USER-Pfad als E-Mail-Prefill verwendet.",
    )
    target_person = models.ForeignKey(
        "accounts.Person",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="incoming_invitations",
        verbose_name="Ziel-Person",
        help_text="Gesetzt: Einladung zielt auf bestehende Person (One-Click-Join). Leer: Neu-USER mit Passkey-Enrollment.",
    )
    mode = models.CharField(
        "Modus",
        max_length=20,
        choices=Mode.choices,
        default=Mode.SELF,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField("läuft ab", default=_default_invite_expiry)
    consumed_at = models.DateTimeField("eingelöst am", null=True, blank=True)

    class Meta:
        verbose_name = "Listen-Einladung"
        verbose_name_plural = "Listen-Einladungen"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        target = self.target_person or self.target_email
        return f"{target} → {self.list}"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def is_usable(self) -> bool:
        return not (self.is_consumed or self.is_expired)
