"""Form viewing: an access-gated page that renders a Form's ordered parts —
static HTML blocks and dynamic embedded lists — plus an asset-serving endpoint.

Forms are authored/managed by super-admins in the Django Admin (Phase 7b ships
no self-service form builder). These views are the read surface.
"""
from __future__ import annotations

import re

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.safestring import mark_safe

from lists.models import ListTemplate
from lists.visibility import build_composed_rows, build_visible_rows

from .models import Form, FormPartAsset
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
    for part in form.parts.select_related("list", "list__template").all():
        # Dynamic list part: rendered with the viewer's own per-field /
        # subject-name visibility (same helpers as the list-detail page).
        # via_associate lists use the wide multi-person row composer.
        rows = None
        composed = False
        if part.list_id:
            composed = (
                part.list.template.member_subject_mode
                == ListTemplate.MemberSubjectMode.VIA_ASSOCIATE
            )
            rows = (
                build_composed_rows(request.user, part.list)
                if composed
                else build_visible_rows(request.user, part.list)
            )
        parts.append(
            {
                "part": part,
                "body": _render_body(part.body, form.pk) if part.body else "",
                "rows": rows,
                "composed": composed,
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
