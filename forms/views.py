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

from lists.visibility import build_visible_rows

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
        parts.append(
            {
                "part": part,
                "body": _render_body(part.body, form.pk) if part.body else "",
                # Dynamic list part: rendered with the viewer's own per-field /
                # subject-name visibility (same helper as the list-detail page).
                "rows": build_visible_rows(request.user, part.list) if part.list_id else None,
            }
        )
    return render(request, "forms/detail.html", {"form_obj": form, "parts": parts})


@login_required
def form_asset(request, form_pk: int, asset_pk: int):
    form = get_object_or_404(Form, pk=form_pk)
    if not can_user_access_form(request.user, form):
        return HttpResponseForbidden("Sie haben keinen Zugriff auf dieses Formular.")
    asset = get_object_or_404(FormPartAsset, pk=asset_pk, part__form=form)
    return HttpResponse(bytes(asset.data), content_type=asset.mime_type)
