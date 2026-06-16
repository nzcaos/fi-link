"""Matrix end-user surface (docs/matrix-implementation-plan.md, Phase 2).

The credentials page: a logged-in parent sees their Matrix access data (Weg A —
homeserver, user id, server-assigned password) as text and as a QR code, to
enter into Element. The Matrix account is provisioned lazily on first visit.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from .client import MatrixError
from .service import ensure_matrix_account

log = logging.getLogger(__name__)


@login_required
def messenger_access(request: HttpRequest) -> HttpResponse:
    if not settings.MATRIX_ENABLED:
        return render(request, "matrix/access.html", {"matrix_enabled": False})

    try:
        account = ensure_matrix_account(request.user)
    except MatrixError as exc:
        log.warning("matrix: provisioning failed for user %s: %s", request.user.pk, exc)
        return render(request, "matrix/access.html", {"matrix_enabled": True, "provision_error": True})

    # The QR is a convenience to read the credentials onto a phone — Weg A has
    # no one-tap QR login (that is Weg C/MSC4108, a later expansion). Real
    # password login in Element still means picking the homeserver + typing.
    qr_text = (
        f"Homeserver: {settings.RP_ORIGIN}\n"
        f"Benutzer: {account.matrix_user_id}\n"
        f"Passwort: {account.password}"
    )
    return render(
        request,
        "matrix/access.html",
        {
            "matrix_enabled": True,
            "account": account,
            "homeserver_url": settings.RP_ORIGIN,
            "server_name": settings.MATRIX_SERVER_NAME,
            "qr_text": qr_text,
        },
    )
