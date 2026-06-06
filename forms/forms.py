"""Forms for the signup surface.

Both slot signups and contributions choose name/email visibility at the moment
of signing up (a dedicated small page), and both stay editable afterward.
Contributions additionally carry a free-text field whose `required` flag depends
on the part's `contribution_required`.
"""
from __future__ import annotations

from django import forms

from .models import FormPart


class SlotSignupForm(forms.Form):
    name_visible = forms.BooleanField(label="Name sichtbar", required=False, initial=True)
    email_visible = forms.BooleanField(label="E-Mail sichtbar", required=False, initial=False)


class ContributionForm(forms.Form):
    contribution_text = forms.CharField(max_length=300)
    name_visible = forms.BooleanField(label="Name sichtbar", required=False, initial=True)
    email_visible = forms.BooleanField(label="E-Mail sichtbar", required=False, initial=False)

    def __init__(self, *args, part: FormPart, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contribution_text"].label = part.contribution_label or "Mein Beitrag"
        self.fields["contribution_text"].required = part.contribution_required
