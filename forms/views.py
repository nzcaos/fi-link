"""Form viewing: an access-gated page that renders a Form's ordered parts —
static HTML blocks plus (from Phase 2) slot/contribution signup parts — and an
asset-serving endpoint.

Forms are authored/managed by super-admins in the Django Admin (no self-service
form builder). These views are the read/signup surface.
"""
from __future__ import annotations

import re

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.safestring import mark_safe

from .models import Form, FormPart, FormPartAsset, FormSignup
from .permissions import (
    _is_own,
    can_user_access_form,
    can_user_admin_form,
    can_user_see_signup_email,
    can_user_see_signup_name,
)

_ASSET_RE = re.compile(r"\[\[asset:(\d+)\]\]")


def _render_body(body: str, form_pk: int) -> str:
    """Substitute ``[[asset:<id>]]`` placeholders with the asset-serve URL and
    return the HTML as safe markup.

    The body is authored by a super-admin (the only role that can create forms),
    so it is rendered trusted — mark_safe is intentional here. The asset URL is
    built via reverse(); the asset view re-checks that the asset belongs to this
    form, so an id pointing elsewhere 404s rather than leaking.
    """
    def repl(match: re.Match) -> str:
        return reverse(
            "forms:asset",
            kwargs={"form_pk": form_pk, "asset_pk": int(match.group(1))},
        )

    return mark_safe(_ASSET_RE.sub(repl, body))


def _signup_cell(viewer, signup: FormSignup) -> dict:
    """One filled position / contribution row, with name/email resolved through
    the per-signup visibility switches (None where hidden → template shows
    'vergeben' / 'anonym')."""
    person = signup.user.person
    return {
        "signup": signup,
        "name": person.full_name if can_user_see_signup_name(viewer, signup) else None,
        "email": person.email if can_user_see_signup_email(viewer, signup) else None,
        "is_own": _is_own(viewer, signup),
    }


def _build_part(viewer, form: Form, part: FormPart) -> dict:
    """Assemble one render entry for a part, branching on ``kind``. Relies on
    ``slots`` + ``signups`` being prefetched (see ``_render_form``)."""
    entry = {
        "part": part,
        "kind": part.kind,
        "body": "",
        "slots": [],
        "contributions": [],
        "contribution_label": part.contribution_label,
        "my_contribution": None,
    }
    if part.kind == FormPart.Kind.HTML:
        entry["body"] = _render_body(part.body, form.pk) if part.body else ""
        return entry

    signups = list(part.signups.all())
    # Cache the FK chain so the visibility helpers don't re-query per signup.
    part.form = form
    for s in signups:
        s.part = part

    if part.kind == FormPart.Kind.SLOTS:
        by_slot: dict[int, list[FormSignup]] = {}
        for s in signups:
            by_slot.setdefault(s.slot_id, []).append(s)
        for slot in part.slots.all():
            filled = [_signup_cell(viewer, s) for s in by_slot.get(slot.id, [])]
            free = max(slot.capacity - len(filled), 0)
            entry["slots"].append(
                {"slot": slot, "filled": filled, "free": free, "is_full": free == 0}
            )
    else:  # CONTRIBUTIONS
        for s in signups:
            cell = _signup_cell(viewer, s)
            cell["text"] = s.contribution_text
            entry["contributions"].append(cell)
            if cell["is_own"]:
                entry["my_contribution"] = s
    return entry


def _render_form(request, form: Form) -> dict:
    """Build the template context for viewing a form (shared by the access-gated
    `form_detail` and, from Phase 4, the broadcast-link `shared` view)."""
    parts_qs = form.parts.prefetch_related(
        "slots",
        Prefetch(
            "signups",
            queryset=FormSignup.objects.select_related("user__person"),
        ),
    )
    parts = [_build_part(request.user, form, part) for part in parts_qs]
    return {
        "form_obj": form,
        "parts": parts,
        "can_signup": form.is_open,
        "is_form_admin": can_user_admin_form(request.user, form),
    }


@login_required
def form_index(request):
    if request.user.is_superuser:
        forms = Form.objects.all()
    else:
        forms = Form.objects.filter(access__user=request.user).distinct()
    return render(request, "forms/index.html", {"forms": forms})


@login_required
def form_detail(request, pk: int):
    form = get_object_or_404(Form, pk=pk)
    if not can_user_access_form(request.user, form):
        return HttpResponseForbidden("Sie haben keinen Zugriff auf dieses Formular.")
    return render(request, "forms/detail.html", _render_form(request, form))


@login_required
def form_asset(request, form_pk: int, asset_pk: int):
    form = get_object_or_404(Form, pk=form_pk)
    if not can_user_access_form(request.user, form):
        return HttpResponseForbidden("Sie haben keinen Zugriff auf dieses Formular.")
    asset = get_object_or_404(FormPartAsset, pk=asset_pk, part__form=form)
    resp = HttpResponse(bytes(asset.data), content_type=asset.mime_type)
    # Defense-in-depth: assets are super-admin-uploaded, but an SVG/HTML blob
    # served same-origin with a sniffable type would be a stored-XSS vector if
    # opened in a document context. `nosniff` pins the declared Content-Type
    # (images keep rendering via <img>); the CSP `sandbox` neutralises any
    # scripting should the URL be navigated to directly. Neither header breaks
    # the inline-image embedding via [[asset:<id>]].
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp
