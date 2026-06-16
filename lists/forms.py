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

from accounts.models import Person, User

from .models import (
    AdminInviteToken,
    List,
    ListAccess,
    ListAttribute,
    ListRecord,
    ListRecordAccess,
    ListRecordValue,
    ListTemplate,
    PersonRelationship,
    RecordManager,
)
from .permissions import (
    can_user_create_top_level_list,
    can_user_edit_record_visibility,
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

    def clean_title(self):
        # Collapse whitespace (including embedded CR/LF) so the title cannot
        # smuggle line breaks into mail-Subject headers — see review M9.
        # Caps at the model's max_length (200) to be safe.
        raw = self.cleaned_data.get("title") or ""
        cleaned = " ".join(raw.split())
        if not cleaned:
            raise ValidationError("Pflichtfeld.")
        return cleaned[:200]

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

    def __init__(self, *args, list_obj, inviting_user, **kwargs):
        super().__init__(*args, **kwargs)
        self.list_obj = list_obj
        self.inviting_user = inviting_user
        # Restrict the dropdown to Persons the inviter actually has business
        # with (see review M6 / candidate_invite_persons) — prevents the
        # full-installation Person-enumeration leak via the dropdown.
        from .permissions import candidate_invite_persons

        self.fields["target_person"].queryset = candidate_invite_persons(
            inviting_user, list_obj
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
_NAME_VIS_FIELD = "vis_name"
_EMAIL_VIS_FIELD = "vis_email"


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
    # "Alle in dieser Liste" is the public (audience NULL) catch-all — everyone
    # who may see the list. The list's OWN Benutzergruppe is deliberately NOT a
    # separate option: for the own list it is identical to "public" (a migration
    # folds pre-existing own-list grants into NULL). The remaining options target
    # DIFFERENT collectives in the hierarchy (parent list / sub-lists).
    options: list[tuple[str, str]] = [("public", "Alle in dieser Liste")]
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
        # B3: only RecordManagers / subject's-own-USER / super-admin may
        # toggle the visibility matrix. List-admins-without-RecordManager-row
        # see the checkboxes disabled and any submitted vis_* values are
        # ignored in save().
        self.user_can_edit_visibility = can_user_edit_record_visibility(user, record)
        self._attribute_fields: dict[int, ListAttribute] = {}
        self._visibility_fields: dict[int, ListAttribute] = {}

        # Subject-name editing: a RecordManager/super-admin may correct the name
        # of a subject who has NOT self-registered (no own USER) — e.g. a parent
        # who entered their partner and child into a class list. Once the person
        # has their own USER they manage the name via "Mein Profil", so it is not
        # offered here (avoids a manager overriding a self-registered person, and
        # avoids two edit surfaces). Same B3 gate as the visibility matrix —
        # the name is PERSON-owned data, not something a mere list-admin edits.
        self.name_editable = self.user_can_edit_visibility and not User.objects.filter(
            person=record.subject
        ).exists()
        if self.name_editable:
            self.fields["subject_given_name"] = forms.CharField(
                label="Vorname", max_length=200, initial=record.subject.given_name
            )
            self.fields["subject_family_name"] = forms.CharField(
                label="Nachname", max_length=200, initial=record.subject.family_name
            )
            # Same gate as the name: a USER-less subject's email is not yet a
            # login identifier, only a delivery address (e.g. the eltern@ alias)
            # that the manager entered — so the manager may correct it too.
            self.fields["subject_email"] = forms.EmailField(
                label="E-Mail",
                required=False,
                initial=record.subject.email,
                help_text="Leer lassen, wenn nicht bekannt. Wird z. B. für den eltern@-Verteiler genutzt.",
            )

        values = {
            v.attribute_id: v.value
            for v in ListRecordValue.objects.filter(record=record).select_related("attribute")
        }
        access_rows = ListRecordAccess.objects.filter(record=record)
        access_by_attr: dict[int, set[str]] = {}
        name_audiences: set[str] = set()
        email_audiences: set[str] = set()
        for row in access_rows:
            key = "public" if row.audience_id is None else f"list-{row.audience_id}"
            if row.attribute_id is not None:
                access_by_attr.setdefault(row.attribute_id, set()).add(key)
            elif row.sentinel == ListRecordAccess.Sentinel.EMAIL:
                email_audiences.add(key)
            else:
                # name sentinel (legacy NULL sentinel rows are treated as name).
                name_audiences.add(key)

        audience_choices = _audience_choices_for(record.list)

        # M8: subject-name visibility row at the top of the matrix. Same
        # audience-choices and same B3 gate as the attribute rows.
        self.fields[_NAME_VIS_FIELD] = forms.MultipleChoiceField(
            label="Sichtbar für (Name)",
            choices=audience_choices,
            widget=forms.CheckboxSelectMultiple,
            required=False,
            initial=sorted(name_audiences),
            disabled=not self.user_can_edit_visibility,
        )

        # Multi-person row: subject-email visibility, only offered when the
        # subject PERSON actually has an account email to disclose. Defaults to
        # empty (hidden / opt-in) — there is no default public row.
        self.has_email_vis = bool(getattr(record.subject, "email", None))
        if self.has_email_vis:
            self.fields[_EMAIL_VIS_FIELD] = forms.MultipleChoiceField(
                label="Sichtbar für (E-Mail)",
                help_text=(
                    "Konto-E-Mail dieser Person. Standardmäßig verborgen — nur "
                    "sichtbar für die hier ausgewählten Gruppen."
                ),
                choices=audience_choices,
                widget=forms.CheckboxSelectMultiple,
                required=False,
                initial=sorted(email_audiences),
                disabled=not self.user_can_edit_visibility,
            )

        # Only the attributes that belong to THIS record's role are editable
        # here: a member record (e.g. the child) edits member-role attributes, an
        # associate record (e.g. a parent) edits associate-role ones. Without this
        # the family dialog offered each parent the member attributes too (the
        # child's address grid), which aren't shown for the parent in the list and
        # are edited where that person is a member instead. For self-mode lists
        # every attribute is member-role and the record is a member, so this is a
        # no-op there.
        self._role_attributes = [
            a
            for a in record.list.template.attributes.all()
            if a.applies_to_role == record.role
        ]
        for attribute in self._role_attributes:
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
                disabled=not self.user_can_edit_visibility,
            )
            self._visibility_fields[attribute.pk] = attribute

    def iter_attribute_rows(self):
        """Render-helper: yields (attribute, value_field, visibility_field|None)."""
        for attribute in self._role_attributes:
            value_field = self[f"{_ATTR_FIELD_PREFIX}{attribute.pk}"]
            vis_field = None
            vis_key = f"{_AUDIENCE_FIELD_PREFIX}{attribute.pk}"
            if vis_key in self.fields:
                vis_field = self[vis_key]
            yield attribute, value_field, vis_field

    @transaction.atomic
    def save(self) -> ListRecord:
        # N13: serialize concurrent saves on the same record. Without this
        # lock two parallel POSTs (e.g. owner with two browser tabs open on
        # the same record-edit form, or an admin moderating while the owner
        # tweaks visibility) would both run the delete+insert sequence on
        # LIST_RECORD_ACCESS. The second tx's `delete(... attribute=X)` then
        # wipes the first tx's freshly-inserted rows — the visibility matrix
        # appears to silently reset. A row-level lock on the record blocks
        # the second writer until the first commits; the second then re-runs
        # delete+insert against the now-committed state, which is the
        # intended last-writer-wins semantics.
        ListRecord.objects.select_for_update().get(pk=self.record.pk)

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
        # B3: skip the entire matrix-write block when the saving user is not
        # permitted to edit visibility (list-admin without RecordManager row).
        # Defense-in-depth on top of the disabled-field gate in __init__: even
        # a tampered POST cannot mutate the matrix.
        if self.user_can_edit_visibility:
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

            # M8: subject-name visibility — same delete-then-insert pattern,
            # but with attribute=None + sentinel="name". Scoped to the name
            # sentinel so it never touches the email sentinel rows (which the
            # multi-person-row matrix writes via its own block).
            picked_name = self.cleaned_data.get(_NAME_VIS_FIELD) or []
            ListRecordAccess.objects.filter(
                record=self.record,
                attribute__isnull=True,
                sentinel=ListRecordAccess.Sentinel.NAME,
            ).delete()
            for key in picked_name:
                audience_id = _audience_key_to_list_id(key)
                ListRecordAccess.objects.create(
                    record=self.record,
                    attribute=None,
                    sentinel=ListRecordAccess.Sentinel.NAME,
                    audience_id=audience_id,
                )

            # Multi-person row: subject-email visibility — same pattern, scoped
            # to the email sentinel. Only when the email row was offered.
            if self.has_email_vis:
                picked_email = self.cleaned_data.get(_EMAIL_VIS_FIELD) or []
                ListRecordAccess.objects.filter(
                    record=self.record,
                    attribute__isnull=True,
                    sentinel=ListRecordAccess.Sentinel.EMAIL,
                ).delete()
                for key in picked_email:
                    audience_id = _audience_key_to_list_id(key)
                    ListRecordAccess.objects.create(
                        record=self.record,
                        attribute=None,
                        sentinel=ListRecordAccess.Sentinel.EMAIL,
                        audience_id=audience_id,
                    )

        # Subject name (PERSON-level). Server-side gate mirrors the field
        # presence (manager/super + subject has no own USER); a tampered POST
        # without that gate is ignored. PERSON changes are list-wide, hence the
        # strict gate.
        if self.name_editable:
            person = self.record.subject
            person.given_name = self.cleaned_data["subject_given_name"].strip()
            person.family_name = self.cleaned_data["subject_family_name"].strip()
            # Normalise blank to NULL so the eltern@ resolver (PERSON.email IS
            # NOT NULL) doesn't pick up an empty address.
            person.email = (self.cleaned_data.get("subject_email") or "").strip() or None
            person.save(update_fields=["given_name", "family_name", "email", "updated_at"])

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


# ---------------------------------------------------------------------------
# Associate-wizard (Phase 3b-2)
# ---------------------------------------------------------------------------


_DEFAULT_ROLES = [
    "Mutter von",
    "Vater von",
    "Erziehungsberechtigte von",
]

# Field-name prefixes for the associate wizard. Member (child) attributes reuse
# the generic `attr_` prefix; the two parents get their own namespaces so each
# parent's associate-role attribute values stay separate.
_P1_ATTR_PREFIX = "p1_attr_"
_P2_ATTR_PREFIX = "p2_attr_"


class TestSendForm(forms.Form):
    """Phase 4 super-admin test-send: triggers a real SMTP fan-out across the
    list's Benutzergruppe.

    Defense-in-depth: subject is whitespace-collapsed (M9) before reaching
    the mail-task, in addition to the task-side sanitizer.
    """

    subject = forms.CharField(
        label="Betreff",
        max_length=200,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    body = forms.CharField(
        label="Nachricht",
        widget=forms.Textarea(attrs={"rows": 6}),
    )

    def clean_subject(self):
        raw = self.cleaned_data.get("subject") or ""
        cleaned = " ".join(raw.split())
        if not cleaned:
            raise ValidationError("Pflichtfeld.")
        return cleaned

    def clean_body(self):
        body = self.cleaned_data.get("body") or ""
        if not body.strip():
            raise ValidationError("Pflichtfeld.")
        return body


def _attr_value_str(attribute: ListAttribute, raw) -> str:
    if attribute.type == ListAttribute.Type.CHECKBOX:
        return "true" if raw else "false"
    if raw in (None, ""):
        return ""
    return str(raw)


class AssociateWizardForm(forms.Form):
    """Single-page onboarding wizard for `via_associate` lists.

    The registering USER declares the member PERSON (e.g. their child) and
    captures up to two associated persons (the parents/guardians). The member
    and the parents are *separate* records joined by PersonRelationship — see
    CLAUDE.md / *Family-association model / Multi-person row*. Attribute values
    are split by `ListAttribute.applies_to_role`: member-role attributes land on
    the child record, associate-role attributes on each parent record.

    One POST creates, atomically:
      - child PERSON + member ListRecord (role=member) + member-role values;
      - PersonRelationship (child ← registering user) + RecordManager(guardian)
        on the child + ListAccess (the user joins the Benutzergruppe);
      - the registering user's own associate ListRecord (role=associate,
        subject=user.person) + associate-role values + RecordManager
        (self_registered) — reused if it already exists (sibling in the same
        class);
      - optionally a second parent: PERSON + associate ListRecord +
        PersonRelationship + RecordManager(creator, proxy) + associate values.

    Visibility matrix is intentionally not part of this form, but sensible
    defaults are written so the class list reproduces the paper class list
    (CLAUDE.md / *List display defaults*): the M8 signal sets the name default
    (public), every non-`must_be_public` attribute (address, phone, …) is
    granted to the list's own Benutzergruppe, and each record's email stays
    hidden (opt-in). The saver refines all of these in the regular record-edit
    form afterwards.
    """

    given_name = forms.CharField(
        label="Vorname des Mitglieds (z. B. Kind)",
        max_length=200,
    )
    family_name = forms.CharField(
        label="Nachname des Mitglieds",
        max_length=200,
    )
    member_email = forms.EmailField(
        label="E-Mail des Mitglieds (optional)",
        required=False,
        help_text=(
            "Nur ausfüllen, wenn das Mitglied eine eigene E-Mail-Adresse hat. "
            "Kinder bleiben meist leer."
        ),
    )
    role = forms.ChoiceField(
        label="Ihre Rolle gegenüber dem Mitglied",
        choices=[],
    )

    add_second_parent = forms.BooleanField(
        label="Zweite Bezugsperson hinzufügen (z. B. zweiter Elternteil)",
        required=False,
    )
    p2_given_name = forms.CharField(
        label="Vorname der zweiten Bezugsperson", max_length=200, required=False
    )
    p2_family_name = forms.CharField(
        label="Nachname der zweiten Bezugsperson", max_length=200, required=False
    )
    p2_email = forms.EmailField(
        label="E-Mail der zweiten Bezugsperson (optional)",
        required=False,
        help_text=(
            "Optional. Sie können diese Person später per Einladung selbst "
            "übernehmen lassen — dann setzt sie ihre Freigaben selbst."
        ),
    )
    p2_role = forms.ChoiceField(
        label="Rolle der zweiten Bezugsperson", choices=[], required=False
    )

    def __init__(self, *args, list_obj: List, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.list_obj = list_obj
        self.user = user

        roles = list_obj.template.relationship_roles or _DEFAULT_ROLES
        role_choices = [(r, r) for r in roles]
        self.fields["role"].choices = role_choices
        self.fields["p2_role"].choices = [("", "—")] + role_choices

        # Split attributes by applies_to_role: member-role onto the child,
        # associate-role onto each parent.
        self._member_attrs: dict[int, ListAttribute] = {}
        self._associate_attrs: dict[int, ListAttribute] = {}
        for attribute in list_obj.template.attributes.all():
            if attribute.applies_to_role == ListAttribute.AppliesTo.ASSOCIATE:
                self._associate_attrs[attribute.pk] = attribute
                # Parent 1 keeps the template's required-ness; parent 2 is
                # always optional (the whole second-parent block is optional).
                self.fields[f"{_P1_ATTR_PREFIX}{attribute.pk}"] = _build_field_for_attribute(attribute)
                p2_field = _build_field_for_attribute(attribute)
                p2_field.required = False
                self.fields[f"{_P2_ATTR_PREFIX}{attribute.pk}"] = p2_field
            else:
                self._member_attrs[attribute.pk] = attribute
                self.fields[f"{_ATTR_FIELD_PREFIX}{attribute.pk}"] = _build_field_for_attribute(attribute)

    def iter_attribute_rows(self):
        """Render-helper: member (child) attribute rows — (attribute, field)."""
        for pk, attribute in self._member_attrs.items():
            yield attribute, self[f"{_ATTR_FIELD_PREFIX}{pk}"]

    def iter_p1_attribute_rows(self):
        """Render-helper: registering parent's associate attribute rows."""
        for pk, attribute in self._associate_attrs.items():
            yield attribute, self[f"{_P1_ATTR_PREFIX}{pk}"]

    def iter_p2_attribute_rows(self):
        """Render-helper: second parent's associate attribute rows."""
        for pk, attribute in self._associate_attrs.items():
            yield attribute, self[f"{_P2_ATTR_PREFIX}{pk}"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("add_second_parent"):
            for field, label in (
                ("p2_given_name", "Vorname"),
                ("p2_family_name", "Nachname"),
                ("p2_role", "Rolle"),
            ):
                if not cleaned.get(field):
                    self.add_error(
                        field,
                        f"{label} der zweiten Bezugsperson ist erforderlich, "
                        "wenn Sie eine zweite Bezugsperson hinzufügen.",
                    )
        return cleaned

    def _write_values(self, record, attrs: dict, prefix: str) -> None:
        for pk, attribute in attrs.items():
            ListRecordValue.objects.create(
                record=record,
                attribute=attribute,
                value=_attr_value_str(attribute, self.cleaned_data.get(f"{prefix}{pk}")),
            )
            # School-class density: a via_associate list reproduces the paper
            # class list, where address/phone are visible to the class (see
            # CLAUDE.md / *List display defaults*). The matrix is not part of
            # this wizard, so without a default grant these fields would stay
            # hidden to every non-privileged member. Grant each non-public
            # attribute to the list's own Benutzergruppe (audience = the list).
            # `must_be_public` attrs are always visible (no row needed); the
            # name sentinel is defaulted public by the post_save signal and the
            # email sentinel stays opt-in — neither is touched here.
            if not attribute.must_be_public:
                ListRecordAccess.objects.get_or_create(
                    record=record,
                    attribute=attribute,
                    audience=self.list_obj,
                )

    @transaction.atomic
    def save(self) -> ListRecord:
        # 1) Child (member) record + member-role values.
        child = Person.objects.create(
            given_name=self.cleaned_data["given_name"].strip(),
            family_name=self.cleaned_data["family_name"].strip(),
            email=self.cleaned_data.get("member_email") or None,
        )
        record = ListRecord.objects.create(
            list=self.list_obj,
            subject=child,
            role=ListRecord.Role.MEMBER,
        )
        self._write_values(record, self._member_attrs, _ATTR_FIELD_PREFIX)

        # 2) Registering user ↔ child relationship + guardian edit-right + the
        #    user joins the Benutzergruppe. ListAccess is idempotent across
        #    re-runs (a parent adding a second child to the same list).
        PersonRelationship.objects.create(
            subject_person=child,
            related_person=self.user.person,
            role=self.cleaned_data["role"],
        )
        RecordManager.objects.create(
            record=record,
            user=self.user,
            basis=RecordManager.Basis.GUARDIAN,
        )
        ListAccess.objects.get_or_create(list=self.list_obj, user=self.user)

        # 3) The registering user's own associate record (role=associate,
        #    subject=user.person). Reused for a sibling already onboarded into
        #    this list (active record per (list, subject) is unique), so we only
        #    write values / the manager row on first creation. Explicit
        #    filter-then-create because the active-record condition is a query
        #    lookup that get_or_create cannot also use in create().
        p1_record = ListRecord.objects.filter(
            list=self.list_obj,
            subject=self.user.person,
            archived_at__isnull=True,
        ).first()
        if p1_record is None:
            p1_record = ListRecord.objects.create(
                list=self.list_obj,
                subject=self.user.person,
                role=ListRecord.Role.ASSOCIATE,
            )
            self._write_values(p1_record, self._associate_attrs, _P1_ATTR_PREFIX)
            RecordManager.objects.create(
                record=p1_record,
                user=self.user,
                basis=RecordManager.Basis.SELF_REGISTERED,
            )

        # 4) Optional second parent: a new PERSON (not a USER) with their own
        #    associate record, managed by the registering user as proxy creator.
        if self.cleaned_data.get("add_second_parent"):
            p2_person = Person.objects.create(
                given_name=self.cleaned_data["p2_given_name"].strip(),
                family_name=self.cleaned_data["p2_family_name"].strip(),
                email=self.cleaned_data.get("p2_email") or None,
            )
            p2_record = ListRecord.objects.create(
                list=self.list_obj,
                subject=p2_person,
                role=ListRecord.Role.ASSOCIATE,
            )
            PersonRelationship.objects.create(
                subject_person=child,
                related_person=p2_person,
                role=self.cleaned_data["p2_role"],
            )
            RecordManager.objects.create(
                record=p2_record,
                user=self.user,
                basis=RecordManager.Basis.CREATOR,
            )
            self._write_values(p2_record, self._associate_attrs, _P2_ATTR_PREFIX)

        # The view lands on the child record's edit form for refinement.
        return record


# ---------------------------------------------------------------------------
# Lifecycle (Phase 6)
# ---------------------------------------------------------------------------


class CohortEditForm(forms.ModelForm):
    """Edit the school-class cohort metadata on a List (CLAUDE.md / *Cohort
    metadata*). Only meaningful for school-class templates; other templates
    simply leave these null and never participate in rollover.
    """

    class Meta:
        model = List
        fields = ["cohort_grade", "cohort_track", "curriculum_track"]
        widgets = {
            "cohort_track": forms.TextInput(attrs={"autocomplete": "off"}),
        }

    def clean_cohort_track(self):
        raw = (self.cleaned_data.get("cohort_track") or "").strip().lower()
        return raw or None


class TransferInitiateForm(forms.Form):
    """Pick the destination list for a single-PERSON class transfer. Restricted
    to non-archived lists of the *same template* — cross-template moves are not
    supported in v1 (CLAUDE.md / *Class transfer*).
    """

    to_list = forms.ModelChoiceField(
        queryset=None,
        label="Ziel-Liste",
        empty_label="— Ziel-Liste wählen —",
    )

    def __init__(self, *args, source_list: List, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_list = source_list
        self.fields["to_list"].queryset = (
            List.objects.filter(
                archived_at__isnull=True, template=source_list.template
            )
            .exclude(pk=source_list.pk)
            .order_by("title")
        )


class AdminInviteForm(forms.Form):
    """Create an AdminInviteToken: invite a successor (`handover`) or an
    additional admin (`add`). Authentication on click is mandatory — the token
    alone never confers rights (CLAUDE.md / *Admin handover*).
    """

    to_email = forms.EmailField(label="E-Mail-Adresse")
    mode = forms.ChoiceField(
        label="Modus",
        choices=AdminInviteToken.Mode.choices,
        initial=AdminInviteToken.Mode.ADD,
    )

    def clean_to_email(self):
        return (self.cleaned_data.get("to_email") or "").strip().lower()
