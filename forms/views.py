"""Form viewing: an access-gated page that renders a Form's ordered parts —
static HTML blocks plus (from Phase 2) slot/contribution signup parts — and an
asset-serving endpoint.

Forms are authored/managed by super-admins in the Django Admin (no self-service
form builder). These views are the read/signup surface.
"""
from __future__ import annotations

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from .forms import ContributionForm, SlotSignupForm
from .models import (
    Form,
    FormAccess,
    FormPart,
    FormPartAsset,
    FormSignup,
    FormSlot,
    _gen_share_token,
)
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
        # The authored HTML body is a description shown for every kind (above the
        # signup list for slots/contributions, and the sole content for html).
        "body": _render_body(part.body, form.pk) if part.body else "",
        "slots": [],
        "contributions": [],
        "contribution_label": part.contribution_label,
        "my_contribution": None,
    }
    if part.kind == FormPart.Kind.HTML:
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
            slot_signups = by_slot.get(slot.id, [])
            filled = [_signup_cell(viewer, s) for s in slot_signups]
            free = max(slot.capacity - len(filled), 0)
            mine = next((s for s in slot_signups if _is_own(viewer, s)), None)
            entry["slots"].append(
                {
                    "slot": slot,
                    "filled": filled,
                    "free": free,
                    "is_full": free == 0,
                    "mine": mine,
                }
            )
    else:  # CONTRIBUTIONS
        for s in signups:
            cell = _signup_cell(viewer, s)
            cell["text"] = s.contribution_text
            entry["contributions"].append(cell)
            if cell["is_own"]:
                entry["my_contribution"] = s
    return entry


def _render_form(request, form: Form, *, share_token: str | None = None) -> dict:
    """Build the template context for viewing a form (shared by the access-gated
    `form_detail` and, from Phase 4, the broadcast-link `shared` view).

    ``share_token`` is threaded into the signup forms so a broadcast-link visitor
    (who has no FormAccess yet) is authorised by the token; see
    ``_can_user_signup``.
    """
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
        "share_token": share_token,
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


def shared(request, token: str):
    """Broadcast-link view: open to anyone holding the form's share token (no
    login required to *view*). Signing up still needs auth — handled by the
    per-button login handoff in `shared_signin`; authenticated visitors get the
    real signup forms, authorised by the token via `_can_user_signup`."""
    form = get_object_or_404(Form, share_token=token)
    return render(request, "forms/shared.html", _render_form(request, form, share_token=token))


def shared_signin(request, token: str):
    """Anonymous-visitor handoff: stash the token and bounce to login. After a
    successful login/registration `_next_url_after_auth` returns to this form."""
    form = get_object_or_404(Form, share_token=token)
    request.session["pending_form_token"] = form.share_token
    return redirect("accounts:login")


def _can_user_signup(user, form: Form, token: str) -> bool:
    """A user may sign up if the form is open AND either they already have access
    OR they present the form's valid broadcast token (Phase 4). Signing up then
    grants FormAccess, so the token is only needed for the first interaction."""
    if not form.is_open:
        return False
    if can_user_access_form(user, form):
        return True
    return bool(form.share_token) and token == form.share_token


def _signup_redirect(form: Form, token: str):
    """Back to the broadcast view if the visitor came via a token, else the
    access-gated detail page."""
    if token and token == form.share_token:
        return redirect("forms:shared", token=token)
    return redirect("forms:detail", pk=form.pk)


@login_required
def slot_signup(request, pk: int, slot_pk: int):
    """Sign up for a slot, choosing name/email visibility at this step; the same
    page edits the visibility of an existing signup afterward."""
    slot = get_object_or_404(FormSlot, pk=slot_pk, part__form_id=pk)
    form = slot.part.form
    token = request.GET.get("token", "") or request.POST.get("token", "")
    if not _can_user_signup(request.user, form, token):
        return HttpResponseForbidden("Eintragen ist hier nicht möglich.")

    existing = FormSignup.objects.filter(slot=slot, user=request.user).first()

    if request.method == "POST":
        sform = SlotSignupForm(request.POST)
        if sform.is_valid():
            nv = sform.cleaned_data["name_visible"]
            ev = sform.cleaned_data["email_visible"]
            if existing:
                existing.name_visible = nv
                existing.email_visible = ev
                existing.save(update_fields=["name_visible", "email_visible"])
                messages.success(request, "Sichtbarkeit aktualisiert.")
                return _signup_redirect(form, token)
            with transaction.atomic():
                locked = FormSlot.objects.select_for_update().get(pk=slot.pk)
                if FormSignup.objects.filter(slot=locked).count() >= locked.capacity:
                    messages.error(request, "Diese Position ist bereits voll belegt.")
                else:
                    FormSignup.objects.create(
                        part=locked.part, slot=locked, user=request.user,
                        name_visible=nv, email_visible=ev,
                    )
                    FormAccess.objects.get_or_create(form=form, user=request.user)
                    messages.success(request, f"Eingetragen: {locked.label}.")
            return _signup_redirect(form, token)
    else:
        initial = (
            {"name_visible": existing.name_visible, "email_visible": existing.email_visible}
            if existing
            else None
        )
        sform = SlotSignupForm(initial=initial)

    return render(
        request,
        "forms/slot_signup.html",
        {"form_obj": form, "slot": slot, "sform": sform, "token": token, "existing": existing},
    )


@login_required
def contribute(request, pk: int, part_pk: int):
    part = get_object_or_404(
        FormPart, pk=part_pk, form_id=pk, kind=FormPart.Kind.CONTRIBUTIONS
    )
    form = part.form
    token = request.GET.get("token", "") or request.POST.get("token", "")
    if not _can_user_signup(request.user, form, token):
        return HttpResponseForbidden("Eintragen ist hier nicht möglich.")

    existing = FormSignup.objects.filter(
        part=part, slot__isnull=True, user=request.user
    ).first()

    if request.method == "POST":
        cform = ContributionForm(request.POST, part=part)
        if cform.is_valid():
            FormSignup.objects.update_or_create(
                part=part,
                slot=None,
                user=request.user,
                defaults={
                    "contribution_text": cform.cleaned_data["contribution_text"],
                    "name_visible": cform.cleaned_data["name_visible"],
                    "email_visible": cform.cleaned_data["email_visible"],
                },
            )
            FormAccess.objects.get_or_create(form=form, user=request.user)
            messages.success(request, "Ihr Beitrag wurde gespeichert.")
            return _signup_redirect(form, token)
    else:
        initial = (
            {
                "contribution_text": existing.contribution_text,
                "name_visible": existing.name_visible,
                "email_visible": existing.email_visible,
            }
            if existing
            else None
        )
        cform = ContributionForm(initial=initial, part=part)

    return render(
        request,
        "forms/contribute.html",
        {"form_obj": form, "part": part, "cform": cform, "token": token},
    )


@login_required
@require_POST
def signup_remove(request, pk: int, signup_pk: int):
    signup = get_object_or_404(FormSignup, pk=signup_pk, part__form_id=pk)
    form = signup.part.form
    is_admin = can_user_admin_form(request.user, form)
    is_own = signup.user_id == request.user.id
    if not (is_own or is_admin):
        return HttpResponseForbidden("Keine Berechtigung zum Entfernen.")
    # Self-removal requires the form to be open; admins may moderate anytime.
    if is_own and not is_admin and not form.is_open:
        return HttpResponseForbidden("Die Eintragung ist geschlossen.")
    token = request.POST.get("token", "")
    signup.delete()
    messages.success(request, "Eintrag entfernt.")
    return _signup_redirect(form, token)


@login_required
@require_POST
def share_link(request, pk: int):
    """Form-admin action: ensure the form has a share token and surface the
    full broadcast URL (to be mailed to recipients)."""
    form = get_object_or_404(Form, pk=pk)
    if not can_user_admin_form(request.user, form):
        return HttpResponseForbidden("Keine Berechtigung.")
    if not form.share_token:
        form.share_token = _gen_share_token()
        form.save(update_fields=["share_token"])
    url = request.build_absolute_uri(
        reverse("forms:shared", kwargs={"token": form.share_token})
    )
    messages.success(request, f"Freigabe-Link: {url}")
    return redirect("forms:detail", pk=form.pk)


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
