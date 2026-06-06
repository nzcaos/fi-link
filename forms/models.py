"""Composable forms (Phase 7b → self-service signup rework).

A FORM is an ordered sequence of FORM_PARTs. Each part has a ``kind``:

- ``html`` — a static HTML block (``body`` + image assets), authored by a
  super-admin and rendered trusted.
- ``slots`` — a fixed set of FormSlots (positions with a capacity, e.g.
  "Aufbau Freitag 14:00–15:00" for two people). Logged-in users claim a slot
  (a FormSignup with ``slot`` set); a full slot shows "vergeben".
- ``contributions`` — an open free-text signup (e.g. cake donations): each user
  adds one FormSignup with ``contribution_text`` ("Apfelkuchen", "Brezeln", …).

Per-signup name/email visibility is governed by two booleans on FormSignup —
deliberately *not* the lists' audience matrix: a form has no Benutzergruppe, so
visibility is simply "shown to everyone who can see the form, or not". The form
admin (creator) and super-admin always see real names/emails for moderation.

Images referenced from a part's HTML live in FORM_PART_ASSET. FORM_ACCESS grants
a specific USER the right to find the form under /forms/ (auto-created on signup);
super-admins always may, and anyone with the form's ``share_token`` may view.

English class names, German verbose_names (CLAUDE.md / *Code conventions*).
"""
from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models


def _gen_share_token() -> str:
    """Random URL-safe token for a form's broadcast link."""
    return secrets.token_urlsafe(32)


class Form(models.Model):
    title = models.CharField("Titel", max_length=200)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_forms",
        verbose_name="erstellt von",
    )
    share_token = models.CharField(
        "Freigabe-Token",
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        help_text="Token für den Broadcast-Link; wird bei Bedarf erzeugt.",
    )
    is_open = models.BooleanField(
        "Eintragung offen",
        default=True,
        help_text="Wenn deaktiviert, ist nur noch Ansicht möglich, keine neuen Einträge.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Formular"
        verbose_name_plural = "Formulare"
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title


class FormPart(models.Model):
    """One ordered block of a Form, discriminated by ``kind`` (see module docstring)."""

    class Kind(models.TextChoices):
        HTML = "html", "HTML-Inhalt"
        SLOTS = "slots", "Aufgaben / Positionen"
        CONTRIBUTIONS = "contributions", "Freitext-Beiträge"

    form = models.ForeignKey(
        Form,
        on_delete=models.CASCADE,
        related_name="parts",
        verbose_name="Formular",
    )
    order = models.PositiveIntegerField("Reihenfolge", default=0)
    kind = models.CharField(
        "Art",
        max_length=20,
        choices=Kind.choices,
        default=Kind.HTML,
    )
    title = models.CharField("Titel", max_length=200, blank=True)
    body = models.TextField(
        "HTML-Inhalt",
        blank=True,
        help_text=(
            "HTML-Beschreibung dieses Teils. Bei Helfer- und Beitrags-Teilen "
            "wird sie über der Eintragungs-Liste angezeigt. Assets dieses Teils "
            "per [[asset:<ID>]] einbinden (wird beim Rendern durch die Asset-URL "
            "ersetzt)."
        ),
    )
    contribution_label = models.CharField(
        "Beitrags-Beschriftung",
        max_length=200,
        blank=True,
        default="Mein Beitrag",
        help_text="Nur für Art „Freitext-Beiträge“: Beschriftung des Textfelds.",
    )
    contribution_required = models.BooleanField(
        "Beitragstext erforderlich",
        default=True,
        help_text="Nur für Art „Freitext-Beiträge“: muss der Text ausgefüllt werden?",
    )

    class Meta:
        verbose_name = "Formular-Teil"
        verbose_name_plural = "Formular-Teile"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"{self.form.title} · {self.title or f'Teil {self.order}'}"


class FormSlot(models.Model):
    """A position within a ``slots`` part, with a capacity (number of people)."""

    part = models.ForeignKey(
        FormPart,
        on_delete=models.CASCADE,
        related_name="slots",
        verbose_name="Formular-Teil",
    )
    order = models.PositiveIntegerField("Reihenfolge", default=0)
    label = models.CharField("Bezeichnung", max_length=200)
    capacity = models.PositiveIntegerField(
        "Plätze",
        default=1,
        help_text="Wie viele Personen können sich für diese Position eintragen?",
    )

    class Meta:
        verbose_name = "Position"
        verbose_name_plural = "Positionen"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.label


class FormSignup(models.Model):
    """One person's signup. ``slot`` set → a claimed position (slots part);
    ``slot`` NULL → a free-text contribution (contributions part).

    Name/email are read from ``user.person`` (no re-capture). ``name_visible`` /
    ``email_visible`` are the two per-signup visibility switches.
    """

    part = models.ForeignKey(
        FormPart,
        on_delete=models.CASCADE,
        related_name="signups",
        verbose_name="Formular-Teil",
    )
    slot = models.ForeignKey(
        FormSlot,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="signups",
        verbose_name="Position",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="form_signups",
        verbose_name="Benutzer",
    )
    contribution_text = models.CharField(
        "Beitrag",
        max_length=300,
        blank=True,
    )
    name_visible = models.BooleanField("Name sichtbar", default=True)
    email_visible = models.BooleanField("E-Mail sichtbar", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Eintragung"
        verbose_name_plural = "Eintragungen"
        ordering = ["created_at", "id"]
        constraints = [
            # One person at most once per slot (they may still take several
            # *different* slots).
            models.UniqueConstraint(
                fields=["slot", "user"],
                name="uniq_signup_per_slot_user",
            ),
            # One free-text contribution per person per part (editable).
            models.UniqueConstraint(
                fields=["part", "user"],
                condition=models.Q(slot__isnull=True),
                name="uniq_contribution_per_part_user",
            ),
        ]

    def __str__(self) -> str:
        target = self.slot.label if self.slot_id else self.part.title
        return f"{self.user} → {target}"


class FormPartAsset(models.Model):
    """An image/graphic referenced from a FormPart's HTML body."""

    part = models.ForeignKey(
        FormPart,
        on_delete=models.CASCADE,
        related_name="assets",
        verbose_name="Formular-Teil",
    )
    title = models.CharField("Titel", max_length=200, blank=True)
    mime_type = models.CharField("MIME-Typ", max_length=100, default="application/octet-stream")
    data = models.BinaryField("Daten", blank=True, default=b"")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Formular-Asset"
        verbose_name_plural = "Formular-Assets"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.title or f"Asset {self.pk}"


class FormAccess(models.Model):
    """Grants a USER the right to find a Form under /forms/ (the spec's FORM_ACCESS).

    Auto-created when a user signs up via a broadcast link; super-admins always
    may, and anyone holding ``Form.share_token`` may view without a row here.
    """

    form = models.ForeignKey(
        Form,
        on_delete=models.CASCADE,
        related_name="access",
        verbose_name="Formular",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="form_access",
        verbose_name="Benutzer",
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Formular-Zugriff"
        verbose_name_plural = "Formular-Zugriffe"
        unique_together = [("form", "user")]

    def __str__(self) -> str:
        return f"{self.user} → {self.form}"
