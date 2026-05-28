"""Access control for forms. The spec's FORM_ACCESS is the per-user view gate;
super-admins always qualify (they author/manage forms via the Django Admin).
"""
from __future__ import annotations

from .models import Form, FormAccess


def can_user_access_form(user, form: Form) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return FormAccess.objects.filter(form=form, user=user).exists()
