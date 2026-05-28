"""Composable forms (Phase 7b).

A FORM is an ordered sequence of FORM_PARTs. Each part is either a static HTML
block (``body``) or a dynamic part that embeds a LIST (``list``) — or both
(an HTML intro above an embedded list). Images referenced from a part's HTML
live in FORM_PART_ASSET and are served via the asset view. FORM_ACCESS grants a
specific USER the right to view a form; super-admins always may.

Mirrors the spec entities FORM / FORM_PART / FORM_PART_ASSET / FORM_ACCESS
(filink.md). English class names, German verbose_names (CLAUDE.md / *Code
conventions*).
"""
from __future__ import annotations

from django.conf import settings
from django.db import models


class Form(models.Model):
    title = models.CharField("Titel", max_length=200)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_forms",
        verbose_name="erstellt von",
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
    """One ordered block of a Form. ``body`` is author-supplied HTML (rendered
    trusted — forms are super-admin authored); ``list`` makes the part embed a
    live list, rendered with the viewer's per-field visibility. A part may use
    either or both.
    """

    form = models.ForeignKey(
        Form,
        on_delete=models.CASCADE,
        related_name="parts",
        verbose_name="Formular",
    )
    order = models.PositiveIntegerField("Reihenfolge", default=0)
    title = models.CharField("Titel", max_length=200, blank=True)
    body = models.TextField(
        "HTML-Inhalt",
        blank=True,
        help_text=(
            "HTML-Vorlage für diesen Teil. Assets dieses Teils per "
            "[[asset:<ID>]] einbinden (wird beim Rendern durch die Asset-URL "
            "ersetzt)."
        ),
    )
    list = models.ForeignKey(
        "lists.List",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="form_parts",
        verbose_name="dynamischer Teil (Liste)",
        help_text="Optional: bindet diese Liste als dynamischen Teil ein.",
    )

    class Meta:
        verbose_name = "Formular-Teil"
        verbose_name_plural = "Formular-Teile"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"{self.form.title} · {self.title or f'Teil {self.order}'}"


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
    """Grants a USER the right to view a Form (the spec's FORM_ACCESS)."""

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
