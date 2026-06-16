"""Project-wide template context.

`navigation` exposes the logged-in user's "Gefäße" — the lists they admin / are
a member of plus the forms shared with them — as a single flat list. Lists and
forms are deliberately *not* differentiated here: to the end user both are just
containers they can fill in (see CLAUDE.md / the title-switcher + unified
overview decision). The differentiation only survives in the admin surfaces.

The title-switcher partial (`templates/_title_switcher.html`) turns a detail
page's <h1> into a dropdown when this list has more than one entry, so a parent
with siblings in different classes can jump straight between them.
"""
from __future__ import annotations

from django.urls import reverse


def navigation(request):
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return {}

    # Imported lazily so the context processor does not pull the app models in
    # at settings-import time.
    from forms.models import Form
    from lists.permissions import accessible_lists_for

    entries = []
    for lst in accessible_lists_for(user):
        entries.append(
            {
                "title": lst.title,
                "url": reverse("lists:detail", kwargs={"pk": lst.pk}),
                "kind": "list",
                "kind_label": "Liste",
            }
        )

    if user.is_superuser:
        forms_qs = Form.objects.all()
    else:
        forms_qs = Form.objects.filter(access__user=user).distinct()
    for form in forms_qs.order_by("title"):
        entries.append(
            {
                "title": form.title,
                "url": reverse("forms:detail", kwargs={"pk": form.pk}),
                "kind": "form",
                "kind_label": "Formular",
            }
        )

    return {"nav_gefaesse": entries}
