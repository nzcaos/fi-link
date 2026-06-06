"""Forms for the signup surface.

Slot signups read their two visibility switches straight from POST (rendered as
inline checkboxes per free slot), so they need no Form class. Contributions get
a small Form because they carry a free-text field whose `required` flag depends
on the part's `contribution_required`.
"""
from __future__ import annotations

from django import forms

from .models import FormPart


class ContributionForm(forms.Form):
    contribution_text = forms.CharField(max_length=300)
    name_visible = forms.BooleanField(label="Name sichtbar", required=False, initial=True)
    email_visible = forms.BooleanField(label="E-Mail sichtbar", required=False, initial=False)

    def __init__(self, *args, part: FormPart, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contribution_text"].label = part.contribution_label or "Mein Beitrag"
        self.fields["contribution_text"].required = part.contribution_required
