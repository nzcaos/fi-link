"""Access control + per-signup visibility for forms.

`can_user_access_form` is the per-user view gate (the spec's FORM_ACCESS);
super-admins always qualify (they author/manage forms via the Django Admin).

Signup visibility is deliberately *not* the lists' audience matrix — a form has
no Benutzergruppe. Each signup carries two booleans (`name_visible`,
`email_visible`) meaning "shown to everyone who can see the form, or not". The
form admin (creator) and super-admin always see real names/emails for
moderation; a user always sees their own signup. `viewer` may be an
AnonymousUser (Phase 4 broadcast-link view) — the helpers handle that.
"""
from __future__ import annotations

from .models import Form, FormAccess, FormSignup


def can_user_access_form(user, form: Form) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return FormAccess.objects.filter(form=form, user=user).exists()


def can_user_admin_form(user, form: Form) -> bool:
    """Form admins = the creator and the super-admin. They see all names/emails
    and may remove other people's signups (moderation)."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return form.created_by_id == user.id


def _is_own(viewer, signup: FormSignup) -> bool:
    return getattr(viewer, "is_authenticated", False) and signup.user_id == viewer.id


def can_user_see_signup_name(viewer, signup: FormSignup) -> bool:
    if _is_own(viewer, signup):
        return True
    if can_user_admin_form(viewer, signup.part.form):
        return True
    return signup.name_visible


def can_user_see_signup_email(viewer, signup: FormSignup) -> bool:
    if _is_own(viewer, signup):
        return True
    if can_user_admin_form(viewer, signup.part.form):
        return True
    return signup.email_visible
