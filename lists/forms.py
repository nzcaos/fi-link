"""Forms for the lists app.

Phase 3a: list-creation. Phase 3b: record-edit with dynamic fields driven by
the ListTemplate's ListAttribute rows, plus per-(attribute, audience)
visibility checkboxes.
"""
from __future__ import annotations

import re

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import (
    List,
    ListAttribute,
    ListRecord,
    ListRecordAccess,
    ListRecordValue,
    ListTemplate,
    RecordManager,
)
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


# ---------------------------------------------------------------------------
# Record-edit (Phase 3b)
# ---------------------------------------------------------------------------


class ListInviteForm(forms.Form):
    """Admin-side form to create a ListInviteToken.

    target_person is optional; if set, the invitation runs the existing-USER
    one-click path on click and `target_email` is forced to that Person's own
    email (preventing the admin from sending the invitation to a third-party
    address, see review B5). If no Person is picked, `target_email` is taken
    from the form and the recipient runs through passkey enrollment.
    """

    target_email = forms.EmailField(
        label="E-Mail-Adresse des Empfängers",
        required=False,
        help_text=(
            "Bei Auswahl einer bestehenden Person wird automatisch deren "
            "hinterlegte E-Mail-Adresse verwendet."
        ),
    )
    target_person = forms.ModelChoiceField(
        label="Bestehende Person (optional)",
        queryset=None,  # filled in __init__
        required=False,
        help_text=(
            "Leer lassen, wenn die Person noch kein USER ist und sich neu registrieren soll. "
            "Mit Person: Empfänger meldet sich nur per Passkey an, ohne erneute Erfassung."
        ),
    )
    mode = forms.ChoiceField(
        label="Wizard-Modus",
        choices=[("", "Vorlage-Default"), ("self", "Eigene Person ist Mitglied"), ("via_associate", "Mitglied ist eine andere Person")],
        required=False,
    )

    def __init__(self, *args, list_obj, **kwargs):
        from accounts.models import Person

        super().__init__(*args, **kwargs)
        self.list_obj = list_obj
        # Only persons that are Users AND have a stored email can receive an
        # existing-USER invitation (the email is the addressing primitive).
        self.fields["target_person"].queryset = (
            Person.objects.filter(user__isnull=False)
            .exclude(email__isnull=True)
            .exclude(email__exact="")
            .order_by("family_name", "given_name")
        )

    def clean(self):
        cleaned = super().clean()
        target_person = cleaned.get("target_person")
        target_email = (cleaned.get("target_email") or "").strip().lower()

        if target_person is not None:
            person_email = (target_person.email or "").strip().lower()
            if not person_email:
                # Queryset above excludes empty emails, but defensive.
                raise ValidationError(
                    "Die gewählte Person hat keine E-Mail-Adresse hinterlegt — "
                    "Einladung nicht möglich."
                )
            # Force target_email to match the picked Person's stored email.
            # Any value the admin typed is discarded to prevent sending a
            # name-bearing invitation to a third-party address.
            cleaned["target_email"] = person_email
        else:
            if not target_email:
                raise ValidationError(
                    "Bitte eine E-Mail-Adresse angeben oder eine bestehende Person wählen."
                )
            cleaned["target_email"] = target_email
        return cleaned


_ATTR_FIELD_PREFIX = "attr_"
_AUDIENCE_FIELD_PREFIX = "vis_"


def _build_field_for_attribute(attribute: ListAttribute) -> forms.Field:
    label = attribute.name
    if attribute.must_be_public:
        label = f"{label} (öffentlich)"
    required = attribute.must_be_public
    common = {"label": label, "required": required}
    t = attribute.type
    if t == ListAttribute.Type.EMAIL:
        return forms.EmailField(**common)
    if t == ListAttribute.Type.PHONE:
        return forms.CharField(max_length=50, **common)
    if t == ListAttribute.Type.NUMBER:
        return forms.IntegerField(**common)
    if t == ListAttribute.Type.CHECKBOX:
        return forms.BooleanField(required=False, label=label)
    if t == ListAttribute.Type.CHOICE:
        choices = [("", "—")] + [(c, c) for c in (attribute.choices or [])]
        return forms.ChoiceField(choices=choices, **common)
    # USER_RELATIONSHIP is rendered as text for Phase 3b; the relationship UI
    # is wired in Phase 3b-2 (via_associate-Wizard).
    return forms.CharField(max_length=500, widget=forms.TextInput(), **common)


def _audience_choices_for(list_obj: List) -> list[tuple[str, str]]:
    """Audience options offered on the visibility matrix for a record in
    `list_obj`. Format: (key, label). Key is "public" or "list-<id>".
    """
    options: list[tuple[str, str]] = [("public", "Öffentlich")]
    options.append((f"list-{list_obj.pk}", f'Mitglieder von „{list_obj.title}"'))
    if list_obj.parent_id:
        options.append(
            (f"list-{list_obj.parent_id}", f'Mitglieder von „{list_obj.parent.title}"')
        )
    for child in list_obj.children.filter(archived_at__isnull=True):
        options.append((f"list-{child.pk}", f'Mitglieder von „{child.title}"'))
    return options


def _audience_key_to_list_id(key: str) -> int | None:
    if key == "public":
        return None
    if key.startswith("list-"):
        return int(key.split("-", 1)[1])
    raise ValueError(f"Unbekannter audience-Key: {key}")


class RecordEditForm(forms.Form):
    """Dynamic form bound to a single ListRecord. Builds one field per
    ListAttribute of the record's template, plus a visibility-matrix section
    expressed as a MultipleChoiceField per attribute.
    """

    def __init__(self, *args, record: ListRecord, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.record = record
        self.user = user
        self._attribute_fields: dict[int, ListAttribute] = {}
        self._visibility_fields: dict[int, ListAttribute] = {}

        values = {
            v.attribute_id: v.value
            for v in ListRecordValue.objects.filter(record=record).select_related("attribute")
        }
        access_rows = ListRecordAccess.objects.filter(record=record)
        access_by_attr: dict[int, set[str]] = {}
        for row in access_rows:
            key = "public" if row.audience_id is None else f"list-{row.audience_id}"
            access_by_attr.setdefault(row.attribute_id, set()).add(key)

        audience_choices = _audience_choices_for(record.list)

        for attribute in record.list.template.attributes.all():
            value_key = f"{_ATTR_FIELD_PREFIX}{attribute.pk}"
            field = _build_field_for_attribute(attribute)
            initial_raw = values.get(attribute.pk, "")
            if attribute.type == ListAttribute.Type.CHECKBOX:
                field.initial = initial_raw == "true"
            elif attribute.type == ListAttribute.Type.NUMBER:
                field.initial = int(initial_raw) if initial_raw else None
            else:
                field.initial = initial_raw
            self.fields[value_key] = field
            self._attribute_fields[attribute.pk] = attribute

            if attribute.must_be_public:
                continue
            vis_key = f"{_AUDIENCE_FIELD_PREFIX}{attribute.pk}"
            self.fields[vis_key] = forms.MultipleChoiceField(
                label=f"Sichtbar für ({attribute.name})",
                choices=audience_choices,
                widget=forms.CheckboxSelectMultiple,
                required=False,
                initial=sorted(access_by_attr.get(attribute.pk, set())),
            )
            self._visibility_fields[attribute.pk] = attribute

    def iter_attribute_rows(self):
        """Render-helper: yields (attribute, value_field, visibility_field|None)."""
        for attribute in self.record.list.template.attributes.all():
            value_field = self[f"{_ATTR_FIELD_PREFIX}{attribute.pk}"]
            vis_field = None
            vis_key = f"{_AUDIENCE_FIELD_PREFIX}{attribute.pk}"
            if vis_key in self.fields:
                vis_field = self[vis_key]
            yield attribute, value_field, vis_field

    @transaction.atomic
    def save(self) -> ListRecord:
        # 1) Values.
        for pk, attribute in self._attribute_fields.items():
            raw = self.cleaned_data.get(f"{_ATTR_FIELD_PREFIX}{pk}")
            if attribute.type == ListAttribute.Type.CHECKBOX:
                value_str = "true" if raw else "false"
            elif raw in (None, ""):
                value_str = ""
            else:
                value_str = str(raw)
            ListRecordValue.objects.update_or_create(
                record=self.record,
                attribute=attribute,
                defaults={"value": value_str},
            )

        # 2) Visibility matrix: replace existing rows per attribute.
        for pk, attribute in self._visibility_fields.items():
            picked = self.cleaned_data.get(f"{_AUDIENCE_FIELD_PREFIX}{pk}") or []
            ListRecordAccess.objects.filter(record=self.record, attribute=attribute).delete()
            for key in picked:
                audience_id = _audience_key_to_list_id(key)
                ListRecordAccess.objects.create(
                    record=self.record,
                    attribute=attribute,
                    audience_id=audience_id,
                )

        # 3) RecordManager: ensure the saving user is registered as a manager
        # (basis depends on context; default to SELF_REGISTERED if this is the
        # subject's own user, otherwise INVITED via the click handler will have
        # set it already — leave existing rows untouched).
        if (
            self.record.subject_id == getattr(self.user, "person_id", None)
            and not RecordManager.objects.filter(record=self.record, user=self.user).exists()
        ):
            RecordManager.objects.create(
                record=self.record,
                user=self.user,
                basis=RecordManager.Basis.SELF_REGISTERED,
            )

        self.record.save(update_fields=["updated_at"])
        return self.record
