"""Form viewing: an access-gated page that renders a Form's ordered parts —
static HTML blocks plus (from Phase 2) slot/contribution signup parts — and an
asset-serving endpoint.

Forms are authored/managed by super-admins in the Django Admin (no self-service
form builder). These views are the read/signup surface.
"""
from __future__ import annotations

import re

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.safestring import mark_safe

from .models import Form, FormPart, FormPartAsset
from .permissions import can_user_access_form

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

    parts = []
    for part in form.parts.all():
        # Phase 1: only HTML parts render fully; slot/contribution rendering and
        # the signup actions arrive in Phase 2/3. Until then those parts show a
        # placeholder so authored forms remain inspectable without errors.
        parts.append(
            {
                "part": part,
                "body": _render_body(part.body, form.pk)
                if part.kind == FormPart.Kind.HTML and part.body
                else "",
            }
        )
    return render(request, "forms/detail.html", {"form_obj": form, "parts": parts})


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
