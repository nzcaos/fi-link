"""Forms for the lists app (Phase 3a).

For now: list-creation only. Record-edit forms land in Phase 3b together with
the visibility matrix.
"""
from __future__ import annotations

import re

from django import forms
from django.core.exceptions import ValidationError

from .models import List, ListTemplate
from .permissions import (
    can_user_create_top_level_list,
    eligible_parents_for,
)


_ALIAS_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_.-]*[a-z0-9])?$")


class ListCreateForm(forms.ModelForm):
    """Create a new List.

    The set of available parents depends on the user (super-admin sees all
    non-archived lists plus an explicit "kein Parent" option; regular users
    see only lists where they are admin or member, no top-level option).
    """

    class Meta:
        model = List
        fields = ["title", "email_alias", "template", "parent", "visibility"]
        widgets = {
            "title": forms.TextInput(attrs={"autocomplete": "off"}),
            "email_alias": forms.TextInput(attrs={"autocomplete": "off"}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["template"].queryset = ListTemplate.objects.order_by("name")
        parents = eligible_parents_for(user).order_by("title")
        if can_user_create_top_level_list(user):
            self.fields["parent"].queryset = parents
            self.fields["parent"].empty_label = "— kein Parent (Top-Level-Liste) —"
            self.fields["parent"].required = False
        else:
            self.fields["parent"].queryset = parents
            self.fields["parent"].empty_label = None
            self.fields["parent"].required = True
        self.fields["title"].label = "Titel"
        self.fields["email_alias"].label = "E-Mail-Alias (Local-Part)"
        self.fields["email_alias"].help_text = (
            "Nur Kleinbuchstaben, Ziffern, Punkt, Minus, Unterstrich. Z.B. "
            "'5a' ergibt 5a@<MAIL_DOMAIN>."
        )

    def clean_email_alias(self):
        alias = (self.cleaned_data.get("email_alias") or "").strip().lower()
        if not alias:
            raise ValidationError("Pflichtfeld.")
        if not _ALIAS_RE.match(alias):
            raise ValidationError(
                "Nur a–z, 0–9, '.', '-', '_'; muss mit Buchstabe/Ziffer anfangen und enden."
            )
        return alias

    def clean(self):
        cleaned = super().clean()
        parent = cleaned.get("parent")
        if parent is None and not can_user_create_top_level_list(self.user):
            raise ValidationError(
                "Nur Super-Admins legen Top-Level-Listen ohne Parent an. "
                "Bitte eine übergeordnete Liste wählen."
            )
        return cleaned
