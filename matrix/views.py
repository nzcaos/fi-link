"""Matrix end-user surface (docs/matrix-implementation-plan.md, Phase 2).

The credentials page: a logged-in parent sees their Matrix access data (Weg A —
homeserver, user id, server-assigned password) as text and as a QR code, to
enter into Element. The Matrix account is provisioned lazily on first visit.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import User
from lists.models import List, ListAccess
from lists.permissions import can_user_admin_list

from . import service
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


@login_required
def send_message(request: HttpRequest, pk: int) -> HttpResponse:
    """Admin/super-admin compose-and-send to a class room (the "Bus verspätet
    sich" feature). Sent as the service account, so it posts even in broadcast
    mode. Inline send for immediate success/failure feedback.
    """
    lst = get_object_or_404(List, pk=pk)
    if not settings.MATRIX_ENABLED or not lst.matrix_room_enabled:
        return HttpResponseForbidden("Für diese Liste ist kein Matrix-Klassenraum aktiv.")
    if not (request.user.is_superuser or can_user_admin_list(request.user, lst)):
        return HttpResponseForbidden("Nur Admins dürfen an den Klassenraum senden.")

    if request.method == "POST":
        body = (request.POST.get("body") or "").strip()
        if not body:
            messages.error(request, "Bitte eine Nachricht eingeben.")
        else:
            try:
                service.send_to_list(lst, body)
            except MatrixError as exc:
                log.warning("matrix: broadcast to list %s failed: %s", lst.pk, exc)
                messages.error(request, "Nachricht konnte nicht gesendet werden. Bitte später erneut versuchen.")
            else:
                messages.success(request, "Nachricht an den Klassenraum gesendet.")
                return redirect("lists:detail", pk=lst.pk)

    return render(request, "matrix/send.html", {"list_obj": lst})


@login_required
def room_moderation(request: HttpRequest, pk: int) -> HttpResponse:
    """Emergency moderation: ban/unban a member's Matrix account from the room.
    Kicking happens automatically on list-leave; ban is the tool to keep a
    disruptive member out without removing them from the list itself.
    """
    lst = get_object_or_404(List, pk=pk)
    if not settings.MATRIX_ENABLED or not lst.matrix_room_enabled:
        return HttpResponseForbidden("Für diese Liste ist kein Matrix-Klassenraum aktiv.")
    if not (request.user.is_superuser or can_user_admin_list(request.user, lst)):
        return HttpResponseForbidden("Nur Admins dürfen den Klassenraum moderieren.")

    if request.method == "POST":
        action = request.POST.get("action")
        target = User.objects.filter(pk=request.POST.get("user_id")).first()
        if target and action in ("ban", "unban", "invite"):
            try:
                if action == "ban":
                    service.ban_user_from_list(target, lst, reason="von Admin gebannt")
                    messages.success(request, f"{target.person.full_name} aus dem Klassenraum verbannt.")
                elif action == "unban":
                    # Unban alone only lifts the block — Matrix does NOT re-invite.
                    # Re-invite so the person actually comes back into the room.
                    service.unban_user_from_list(target, lst)
                    service.sync_user_into_room(target, lst)
                    messages.success(request, f"Bann für {target.person.full_name} aufgehoben und neu eingeladen.")
                else:  # invite
                    service.sync_user_into_room(target, lst)
                    messages.success(request, f"{target.person.full_name} in den Klassenraum eingeladen.")
            except MatrixError as exc:
                log.warning("matrix: %s on list %s failed: %s", action, lst.pk, exc)
                messages.error(request, "Aktion fehlgeschlagen. Bitte später erneut versuchen.")
        return redirect("matrix:moderation", pk=lst.pk)

    accesses = (
        ListAccess.objects.filter(list=lst, user__matrix_account__isnull=False)
        .select_related("user__person", "user__matrix_account")
        .order_by("user__person__family_name", "user__person__given_name")
    )
    # Annotate each member with their current room membership so the UI shows
    # the state and offers only the action that makes sense. If Synapse can't be
    # reached, fall back to "unknown" (the template then offers all actions).
    try:
        membership = service.room_membership_for_list(lst)
        membership_unknown = False
    except MatrixError as exc:
        log.warning("matrix: membership fetch for list %s failed: %s", lst.pk, exc)
        membership = {}
        membership_unknown = True

    members = []
    for access in accesses:
        mxid = access.user.matrix_account.matrix_user_id
        members.append({"user": access.user, "mxid": mxid, "state": membership.get(mxid)})

    return render(
        request,
        "matrix/moderation.html",
        {"list_obj": lst, "members": members, "membership_unknown": membership_unknown},
    )
