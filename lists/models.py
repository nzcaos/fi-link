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


def _default_join_expiry():
    """48h default for QR-code join tokens.

    Short enough to limit abuse of a lost/discarded printout, long enough to
    cover a parents' evening plus next-day stragglers. See PLAN.md Phase 3b-2.
    """
    return timezone.now() + timedelta(hours=48)


def _default_release_expiry():
    """7-day default for mail-release/approval tokens.

    Matches the IMAP 7-day grace period (CLAUDE.md / "Retention"): after that
    the raw message may be EXPUNGE'd from the catch-all anyway, and we keep the
    `raw_eml` copy in InboundMessage for the metadata-retention window, so a
    release link living longer than the grace would be of dubious value.
    """
    return timezone.now() + timedelta(days=7)


def _gen_alias_token() -> str:
    """Per-outbound-message alias/bounce token.

    16 url-safe bytes → 22 chars; short enough to keep `bounce-<token>@<domain>`
    and `alias-<token>@<domain>` readable, long enough to make the address
    space unguessable. Collision domain is per OutboundMessage row.
    """
    return secrets.token_urlsafe(16)


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

    class AppliesTo(models.TextChoices):
        MEMBER = "member", "Mitglied (z. B. Kind)"
        ASSOCIATE = "associate", "Zugehörige Person (z. B. Elternteil)"

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
    applies_to_role = models.CharField(
        "gilt für",
        max_length=10,
        choices=AppliesTo.choices,
        default=AppliesTo.MEMBER,
        help_text=(
            "Ob dieses Attribut zum Mitglied selbst (z. B. Kind) oder zu den "
            "zugehörigen Personen (z. B. Eltern) gehört. 'Zugehörige Person' "
            "wird pro verknüpfter Person einmal gerendert."
        ),
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
    display_row = models.PositiveSmallIntegerField(
        "Anzeige-Zeile",
        default=1,
        help_text=(
            "Nur für die Schulklassen-Ansicht (Mitglied-Attribute): In welcher "
            "Zeile des Mitglied-Blocks dieses Attribut nebeneinander angeordnet "
            "wird (1 = erste Zeile, 2 = zweite Zeile, …). Innerhalb einer Zeile "
            "entscheidet 'Position' über die Spaltenreihenfolge."
        ),
    )

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
        help_text=(
            "Klassenstufe der Lettern-Klassen (5–10 G8, 5–11 G9). Kursstufe wird "
            "über cohort_track=leer markiert; K1/K2 sind die Stufen oberhalb der "
            "letzten Lettern-Klasse (G8: 11/12, G9: 12/13). Nur Schulklassen-Vorlagen."
        ),
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

    # Matrix messenger config (docs/matrix-implementation-plan.md, Phase 3).
    # Two flag columns only — all Matrix *logic* lives in the `matrix` app; the
    # room (MatrixRoom) is keyed back to this list. `matrix_room_enabled` marks
    # a list as eligible for a class chat room (default off; the introducing
    # migration backfills True for existing cohort/class lists). `matrix_broadcast_only`
    # switches the room to broadcast mode (events_default=50: only admins/PL≥50 send).
    matrix_room_enabled = models.BooleanField(
        "Matrix-Klassenraum aktiv",
        default=False,
        help_text="Wenn aktiv, bekommt diese Liste einen Matrix-Klassenraum (nur Schulklassen).",
    )
    matrix_broadcast_only = models.BooleanField(
        "Matrix-Broadcast-Modus",
        default=False,
        help_text="Wenn aktiv, dürfen nur Admins (Elternvertretung) im Raum senden.",
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

    `attribute` is `NULL` for the two *subject-level* sentinels, disambiguated
    by `sentinel`: `"name"` = the subject's name (M8), `"email"` = the
    subject PERSON's account email (multi-person row). A real attribute row has
    `attribute` set and `sentinel=NULL`. The invariant "at most one row per
    (record, NULL, sentinel, audience)" is enforced by the single writer
    pattern in `RecordEditForm.save()` (delete-then-insert) and the post_save
    signal on `ListRecord` (`get_or_create`). Postgres treats NULL as distinct
    in unique constraints, so the unique_together below does not block dup
    sentinel rows at SQL level.
    """

    class Sentinel(models.TextChoices):
        NAME = "name", "Name"
        EMAIL = "email", "E-Mail"

    record = models.ForeignKey(
        ListRecord,
        on_delete=models.CASCADE,
        related_name="access",
        verbose_name="Eintrag",
    )
    attribute = models.ForeignKey(
        ListAttribute,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="Attribut",
        help_text="Leer = Subject-Sentinel (siehe 'Sentinel').",
    )
    sentinel = models.CharField(
        "Sentinel",
        max_length=10,
        null=True,
        blank=True,
        choices=Sentinel.choices,
        help_text=(
            "Nur wenn 'Attribut' leer ist: 'name' = Sichtbarkeit des "
            "Subject-Namens (M8), 'email' = Sichtbarkeit der Konto-E-Mail."
        ),
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
        if self.attribute_id:
            attribute = self.attribute.name
        elif self.sentinel == self.Sentinel.EMAIL:
            attribute = "(E-Mail)"
        else:
            attribute = "(Name)"
        return f"{self.record} · {attribute} → {audience}"


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
# Aggregate aliases (Phase 7a — CLAUDE.md / "Aggregate email aliases")
# ---------------------------------------------------------------------------


class AggregateAlias(models.Model):
    """An email address that fans out across multiple lists by relationship
    role — e.g. ``eltern@<domain>`` reaches every Person who is a parent/guardian
    (``included_roles``) of a member inside ``scope_list``'s subtree.

    Not a List: it has no Benutzergruppe and no admins. Recipients are resolved
    at *send time* from PERSON_RELATIONSHIP, never cached. Send permission is
    derived from the LIST_SEND_PERMISSION graph (there is no separate permission
    table here): the sender must hold send permission on **every** target list
    the alias resolves to. Configuration is super-admin only (Django Admin).
    """

    email_alias = models.CharField(
        "E-Mail-Alias",
        max_length=100,
        unique=True,
        help_text="Local-Part der Verteiler-Adresse, z.B. 'eltern' für eltern@<MAIL_DOMAIN>.",
    )
    title = models.CharField("Titel", max_length=200)
    scope_list = models.ForeignKey(
        List,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="aggregate_aliases",
        verbose_name="Geltungsbereich (Liste)",
        help_text=(
            "Leer = alle Listen. Gesetzt: nur dieser Teilbaum (Liste + Unter-Listen). "
            "PROTECT: eine als Geltungsbereich genutzte Liste lässt sich nicht löschen, "
            "ohne den Alias vorher umzukonfigurieren — sonst würde der Bereich still "
            "auf 'alle Listen' aufgeweitet."
        ),
    )
    included_roles = models.JSONField(
        "Einbezogene Rollen",
        default=list,
        blank=True,
        help_text='PersonRelationship-Rollen, z.B. ["Mutter von", "Vater von", "Erziehungsberechtigte von"].',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_aggregate_aliases",
        verbose_name="erstellt von",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Aggregat-Alias (Verteiler)"
        verbose_name_plural = "Aggregat-Aliase (Verteiler)"
        ordering = ["email_alias"]

    def __str__(self) -> str:
        return f"{self.email_alias}@… ({self.title})"

    def clean(self):
        # Normalise + guard against collisions with list addresses and with the
        # reserved bounce-/alias- routing prefixes (lists/inbound.resolve_alias
        # matches list aliases first, so a clash would be silently shadowed).
        from django.core.exceptions import ValidationError

        alias = (self.email_alias or "").strip().lower()
        self.email_alias = alias
        if not alias:
            raise ValidationError({"email_alias": "Darf nicht leer sein."})
        if alias.startswith(("bounce-", "alias-")):
            raise ValidationError(
                {"email_alias": "Reservierter Präfix (bounce-/alias-)."}
            )
        if List.objects.filter(email_alias__iexact=alias).exists():
            raise ValidationError(
                {"email_alias": "Kollidiert mit einer bestehenden Listen-Adresse."}
            )


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


class ListJoinToken(models.Model):
    """A multi-use QR-code join token for a list.

    Differs from `ListInviteToken` (one-shot, personalised):
    - Multi-use: many people can scan the same QR until expiry/revocation.
    - No target_email / target_person: anybody who scans + auths can join.
    - Short default lifetime (48h) — see CLAUDE.md / PLAN.md Phase 3b-2.
      The QR is meant to live on a pinboard at a single event; long lifetimes
      raise the cost of an accidentally-shared printout.

    Click-time the mode (`self` vs. `via_associate`) is read from the LIST's
    LISTTEMPLATE — there is no per-token override.
    """

    token = models.CharField(
        "Token",
        max_length=64,
        unique=True,
        default=_gen_invite_token,
    )
    list = models.ForeignKey(
        List,
        on_delete=models.CASCADE,
        related_name="join_tokens",
        verbose_name="Liste",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_join_tokens",
        verbose_name="erstellt von",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField("läuft ab", default=_default_join_expiry)
    revoked_at = models.DateTimeField("widerrufen am", null=True, blank=True)

    class Meta:
        verbose_name = "Listen-Beitritts-Token (QR)"
        verbose_name_plural = "Listen-Beitritts-Tokens (QR)"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"QR {self.token[:8]}… → {self.list}"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_usable(self) -> bool:
        return not (self.is_expired or self.is_revoked)


# ---------------------------------------------------------------------------
# Outbound mail (Phase 4)
# ---------------------------------------------------------------------------


class OutboundMessage(models.Model):
    """One row per recipient of an outbound list mail.

    Per the spec (CLAUDE.md / "Required state store"), this table feeds bounce
    correlation, reply-routing for anonymisation aliases, OOO/autoresponder
    suppression of replies, and double-receive detection. Phase 4 fills in the
    "we sent this" half; Phase 5b uses the same rows on the inbound side.

    Schema notes:
    - `message_id` is the RFC-2822 Message-ID header value (without angle
      brackets). Globally unique by construction (16 random bytes + host part).
    - `alias_token` is unique per row and used both for the envelope-from
      (`bounce-<token>@<MAIL_DOMAIN>`) and — when anonymisation is in effect —
      for the From: header (`alias-<token>@<MAIL_DOMAIN>`). Reply-routing in
      Phase 5b resolves an inbound to `alias-<token>@…` back to this row's
      original sender.
    - `from_email` is the **original** sender's address. Distinct from the
      envelope-from we actually use; needed by reply-routing and to render
      bounce notifications back to the right human.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "ausstehend"
        SENT = "sent", "versendet"
        FAILED = "failed", "fehlgeschlagen"
        BOUNCED = "bounced", "Bounce"  # filled by Phase 5b DSN correlation

    list = models.ForeignKey(
        List,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="outbound_messages",
        verbose_name="Liste",
        help_text="Gesetzt bei Listen-Mail; leer bei Aggregat-Alias-Versand.",
    )
    aggregate = models.ForeignKey(
        "AggregateAlias",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="outbound_messages",
        verbose_name="Aggregat-Alias",
        help_text="Gesetzt bei Aggregat-Alias-Versand; leer bei normaler Listen-Mail.",
    )
    message_id = models.CharField(
        "Message-ID",
        max_length=255,
        unique=True,
        help_text="RFC-2822-Wert ohne spitze Klammern.",
    )
    alias_token = models.CharField(
        "Alias-Token",
        max_length=64,
        unique=True,
        default=_gen_alias_token,
        help_text="Eindeutig pro Empfänger-Row; speist 'bounce-<token>@' und 'alias-<token>@'.",
    )
    from_email = models.EmailField(
        "Ursprünglicher Absender",
        help_text="Adresse des Original-Senders (für Bounce- und Reply-Routing in Phase 5b).",
    )
    recipient_email = models.EmailField("Empfänger")
    subject = models.CharField("Betreff", max_length=998, blank=True)
    anonymized_from = models.BooleanField(
        "anonymisierte From-Adresse",
        default=False,
        help_text="True: From: wurde auf 'alias-<token>@…' umgeschrieben (Sender-Adress-Schutz).",
    )
    status = models.CharField(
        "Status",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempts = models.PositiveSmallIntegerField("Sende-Versuche", default=0)
    last_error = models.TextField("letzter Fehler", blank=True)
    sent_at = models.DateTimeField("versendet am", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Versandte Nachricht"
        verbose_name_plural = "Versandte Nachrichten"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["list", "-created_at"], name="lists_outbound_list_idx"),
            models.Index(fields=["status"], name="lists_outbound_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(list__isnull=False, aggregate__isnull=True)
                    | models.Q(list__isnull=True, aggregate__isnull=False)
                ),
                name="outbound_exactly_one_source",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.message_id} → {self.recipient_email}"

    @property
    def source_email_alias(self) -> str:
        """Local-part of the From/List-* address — the list's or the aggregate's
        alias, whichever this row carries."""
        return self.list.email_alias if self.list_id else self.aggregate.email_alias

    @property
    def source_title(self) -> str:
        """Human title used in the From display name and List-* headers."""
        return self.list.title if self.list_id else self.aggregate.title


# ---------------------------------------------------------------------------
# Inbound mail (Phase 5a — IMAP IDLE consumer persists; Phase 5b decides)
# ---------------------------------------------------------------------------


class InboundMessage(models.Model):
    """One row per received mail on the catch-all mailbox.

    Phase 5a only persists; the per-message decision (forward, suppress,
    reject, admin-approval) is Phase 5b's job. Idempotency is keyed on the
    composite (UIDVALIDITY, UID) per CLAUDE.md / "Long-lived IMAP IDLE
    consumer": a reconnect that re-fetches an already-seen UID must NOT
    create a duplicate row. UIDVALIDITY participates because IMAP resets
    the UID range when the mailbox is rebuilt.

    `raw_eml` holds the full MIME bytes so Phase 5b's anti-loop / Suppression
    checks (Auto-Submitted, Return-Path, Precedence, In-Reply-To) and the
    DSN parser can operate on the canonical wire form without re-fetching
    from IMAP. Retention is 30–90 days (CLAUDE.md / "Retention") and the
    procrastinate periodic-task in Phase 5b prunes old rows.
    """

    class Decision(models.TextChoices):
        PENDING = "pending", "ausstehend"
        FORWARDED = "forwarded", "weitergeleitet"
        PENDING_APPROVAL = "pending_approval", "Freigabe ausstehend"
        REJECTED = "rejected", "abgewiesen"
        SUPPRESSED = "suppressed", "unterdrückt"
        BOUNCE = "bounce", "Bounce"
        UNKNOWN_ALIAS = "unknown_alias", "Alias unbekannt"

    imap_uidvalidity = models.BigIntegerField(
        "IMAP UIDVALIDITY",
        help_text="UIDVALIDITY der Mailbox zum Zeitpunkt des FETCH.",
    )
    imap_uid = models.BigIntegerField(
        "IMAP UID",
        help_text="Server-vergebene UID der Nachricht — zusammen mit UIDVALIDITY eindeutig.",
    )
    message_id = models.CharField(
        "Message-ID",
        max_length=255,
        blank=True,
        help_text="RFC-2822-Wert ohne spitze Klammern; leer wenn Header fehlt.",
    )
    from_email = models.CharField(
        "Absender",
        max_length=320,
        blank=True,
        help_text="Adress-Teil aus From:; leer bei Parse-Fehler.",
    )
    to_alias = models.CharField(
        "Empfänger-Alias (Local-Part)",
        max_length=200,
        blank=True,
        help_text="Local-Part der ersten passenden Empfänger-Adresse, lowercase, ohne Plus-Tag.",
    )
    to_domain = models.CharField(
        "Empfänger-Domain",
        max_length=255,
        blank=True,
        help_text="Domain-Teil — Phase 5b nutzt das für Multi-Domain-Validation.",
    )
    subject = models.CharField("Betreff", max_length=998, blank=True)
    raw_eml = models.BinaryField(
        "RFC-822-Bytes",
        help_text="Vollständige MIME-Bytes, wie vom IMAP-FETCH geliefert.",
    )
    received_at = models.DateTimeField(
        "empfangen am",
        help_text="IMAP INTERNALDATE wenn verfügbar, sonst Zeitpunkt der FETCH-Antwort.",
    )
    decision = models.CharField(
        "Entscheidung",
        max_length=30,
        choices=Decision.choices,
        default=Decision.PENDING,
    )
    reason = models.TextField(
        "Begründung",
        blank=True,
        help_text="Phase 5b: Grund für SUPPRESSED/REJECTED/UNKNOWN_ALIAS.",
    )
    matched_list = models.ForeignKey(
        List,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inbound_messages",
        verbose_name="Ziel-Liste",
        help_text="Wenn to_alias auf eine Liste matched — vom Consumer gesetzt.",
    )
    matched_outbound = models.ForeignKey(
        OutboundMessage,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inbound_correlations",
        verbose_name="korrelierte ausgehende Nachricht",
        help_text="Gesetzt bei Bounce-Match auf 'bounce-<token>@' (Phase 5b).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Eingegangene Nachricht"
        verbose_name_plural = "Eingegangene Nachrichten"
        ordering = ["-received_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["imap_uidvalidity", "imap_uid"],
                name="lists_inbound_uid_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["message_id"], name="lists_inbound_msgid_idx"),
            models.Index(fields=["to_alias"], name="lists_inbound_toalias_idx"),
            models.Index(
                fields=["decision", "-received_at"],
                name="lists_inbound_decision_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.message_id or '(no Message-ID)'} → {self.to_alias}"


# ---------------------------------------------------------------------------
# Mail release / approval (Phase 5b)
# ---------------------------------------------------------------------------


class MailReleaseToken(models.Model):
    """A pending forward awaiting a confirmation click.

    Two kinds, mirroring the spec's two release paths
    (CLAUDE.md / "Mailing-list behavior"):

    - ``MEMBER`` — the sender is a member of the target list (or a permitted
      cross-list sender whose grant requires a release click). The release
      link is mailed to the **sender**: clicking it proves access to the
      claimed mailbox, which is the anti-spoofing guarantee. The member may
      additionally choose to anonymise their From: address at click time when
      ``offer_anonymize`` is set.
    - ``ADMIN`` — the sender is neither a member nor permitted. The release
      link is mailed to the list's **admins**, who decide whether to forward.

    The token references the InboundMessage rather than copying the body —
    ``InboundMessage.raw_eml`` is the canonical source, re-parsed at click time
    to build the forward. Tokens are swept by a periodic task and cascade away
    with their InboundMessage when metadata retention prunes it.
    """

    class Kind(models.TextChoices):
        MEMBER = "member", "Mitglieder-Freigabe"
        ADMIN = "admin", "Admin-Freigabe"

    class Resolution(models.TextChoices):
        FORWARDED = "forwarded", "weitergeleitet"
        REJECTED = "rejected", "abgelehnt"

    token = models.CharField(
        "Token",
        max_length=64,
        unique=True,
        default=_gen_invite_token,
    )
    inbound = models.ForeignKey(
        "InboundMessage",
        on_delete=models.CASCADE,
        related_name="release_tokens",
        verbose_name="eingegangene Nachricht",
    )
    list = models.ForeignKey(
        List,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="release_tokens",
        verbose_name="Ziel-Liste",
        help_text="Gesetzt bei Listen-Freigabe; leer bei Aggregat-Alias-Freigabe.",
    )
    aggregate = models.ForeignKey(
        "AggregateAlias",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="release_tokens",
        verbose_name="Ziel-Aggregat-Alias",
        help_text="Gesetzt bei Aggregat-Alias-Freigabe (Super-Admin-Approval / Permitted-Release).",
    )
    kind = models.CharField("Art", max_length=20, choices=Kind.choices)
    offer_anonymize = models.BooleanField(
        "Anonymisierung anbieten",
        default=False,
        help_text=(
            "Nur bei MEMBER-Freigabe echter Mitglieder: der Sender darf beim "
            "Klick wählen, ob seine Absender-Adresse durch einen Alias ersetzt "
            "wird."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField("läuft ab", default=_default_release_expiry)
    consumed_at = models.DateTimeField("eingelöst am", null=True, blank=True)
    resolution = models.CharField(
        "Ergebnis",
        max_length=20,
        choices=Resolution.choices,
        blank=True,
    )

    class Meta:
        verbose_name = "Mail-Freigabe-Token"
        verbose_name_plural = "Mail-Freigabe-Tokens"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(list__isnull=False, aggregate__isnull=True)
                    | models.Q(list__isnull=True, aggregate__isnull=False)
                ),
                name="release_token_exactly_one_source",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} release {self.token[:8]}… → {self.list or self.aggregate}"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def is_usable(self) -> bool:
        return not (self.is_consumed or self.is_expired)


# ---------------------------------------------------------------------------
# School-class lifecycle (Phase 6)
# ---------------------------------------------------------------------------


class PendingTransfer(models.Model):
    """A single PERSON's pending move between two lists, awaiting the
    destination-list admin's consent (CLAUDE.md / *Class transfer*).

    Distinct from cohort rollover (which is a batch operation): this models one
    student repeating a grade or switching track. The two-step flow protects
    the destination list — an admin there must accept before the record (and
    its associate records) migrate over.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "ausstehend"
        ACCEPTED = "accepted", "angenommen"
        REJECTED = "rejected", "abgelehnt"

    from_list = models.ForeignKey(
        List,
        on_delete=models.CASCADE,
        related_name="outgoing_transfers",
        verbose_name="von Liste",
    )
    to_list = models.ForeignKey(
        List,
        on_delete=models.CASCADE,
        related_name="incoming_transfers",
        verbose_name="nach Liste",
    )
    person = models.ForeignKey(
        "accounts.Person",
        on_delete=models.CASCADE,
        related_name="pending_transfers",
        verbose_name="Person",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_transfers",
        verbose_name="beantragt von",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        "Status",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    resolved_at = models.DateTimeField("entschieden am", null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="resolved_transfers",
        verbose_name="entschieden von",
    )

    class Meta:
        verbose_name = "Klassenwechsel-Antrag"
        verbose_name_plural = "Klassenwechsel-Anträge"
        ordering = ["-requested_at"]
        constraints = [
            # At most one open transfer per (person, from, to). Resolved rows
            # stay for the audit trail and don't block a future re-request.
            models.UniqueConstraint(
                fields=["person", "from_list", "to_list"],
                condition=models.Q(status="pending"),
                name="unique_pending_transfer_per_person_route",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.person}: {self.from_list} → {self.to_list} ({self.status})"

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING


class AdminInviteToken(models.Model):
    """Hands a list's admin rights to a successor (CLAUDE.md / *Admin handover*).

    Does NOT grant membership — that is `ListInviteToken`'s job. A click alone
    never confers admin rights: the recipient must authenticate (passkey login,
    or registration + enrollment if not yet a USER) before the token is
    consumed. This blocks "compromised mailbox = instant admin takeover".

    - ``add``      — insert the successor as an additional LIST_ADMIN.
    - ``handover`` — insert the successor and, in the same transaction, remove
      the initiating admin's LIST_ADMIN row.
    """

    class Mode(models.TextChoices):
        HANDOVER = "handover", "Übergabe (ich gebe ab)"
        ADD = "add", "Hinzufügen (zusätzlicher Admin)"

    token = models.CharField(
        "Token",
        max_length=64,
        unique=True,
        default=_gen_invite_token,
    )
    list = models.ForeignKey(
        List,
        on_delete=models.CASCADE,
        related_name="admin_invitations",
        verbose_name="Liste",
    )
    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_admin_invitations",
        verbose_name="übergebender Admin",
        help_text="Leer, wenn von einem Super-Admin angestoßen.",
    )
    to_email = models.EmailField(
        "Ziel-E-Mail",
        help_text="Adresse, an die die Admin-Einladung versendet wird.",
    )
    mode = models.CharField(
        "Modus",
        max_length=20,
        choices=Mode.choices,
        default=Mode.ADD,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField("läuft ab", default=_default_invite_expiry)
    consumed_at = models.DateTimeField("eingelöst am", null=True, blank=True)

    class Meta:
        verbose_name = "Admin-Einladung"
        verbose_name_plural = "Admin-Einladungen"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.mode} {self.to_email} → {self.list}"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def is_usable(self) -> bool:
        return not (self.is_consumed or self.is_expired)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


from django.db.models.signals import post_save  # noqa: E402
from django.dispatch import receiver  # noqa: E402


@receiver(post_save, sender=ListRecord)
def _ensure_default_name_visibility(sender, instance, created, **kwargs):
    """M8: every new ListRecord gets a default (attribute=NULL, sentinel="name",
    audience=NULL) ListRecordAccess row — 'subject-name visible to public'. See
    CLAUDE.md / *Subject-name visibility*. Uses get_or_create for idempotency.

    The email sentinel is intentionally *not* defaulted here — parent email
    starts hidden (opt-in), see CLAUDE.md / *Family-association model /
    Multi-person row*.
    """
    if not created:
        return
    ListRecordAccess.objects.get_or_create(
        record=instance,
        attribute=None,
        sentinel=ListRecordAccess.Sentinel.NAME,
        audience=None,
    )
