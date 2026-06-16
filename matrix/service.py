"""High-level Matrix operations (docs/matrix-implementation-plan.md, Phase 2+).

Idempotent, side-effecting helpers that the views, tasks and (later) the
membership-sync layer call. The raw HTTP lives in client.py; this module owns
the policy (pseudonymous localparts/displaynames, encryption, onboarding state).
"""
from __future__ import annotations

import logging
import secrets

from django.conf import settings

from .client import MatrixClient, MatrixError
from .models import MatrixAccount, MatrixRoom, MatrixServiceAccount

log = logging.getLogger(__name__)


def _pseudonymous_displayname(localpart: str) -> str:
    """Non-revealing display name (A1: always pseudonym for the pilot).

    Klarname-bei-Zustimmung is deferred until the name-visibility coupling lands
    (matrix-architecture.md §4). The suffix keeps two parents distinguishable in
    a room without encoding any real identity.
    """
    suffix = localpart.rsplit("-", 1)[-1][-4:]
    return f"Elternteil {suffix}"


def ensure_matrix_account(user) -> MatrixAccount:
    """Return the user's MatrixAccount, provisioning it on first use.

    Idempotent: a second call returns the existing row without touching Synapse.
    The localpart is non-speaking (`u-<hex>`, lowercase — Matrix ids must be
    lowercase) and re-rolled on the rare collision. The server-assigned password
    is stored encrypted so the credentials page can re-display it (Weg A).
    """
    if not settings.MATRIX_ENABLED:
        raise MatrixError("Matrix-Integration ist deaktiviert (MATRIX_ENABLED=False).")

    existing = MatrixAccount.objects.filter(user=user).first()
    if existing:
        return existing

    client = MatrixClient.from_settings()
    password = secrets.token_urlsafe(18)
    last_exc: MatrixError | None = None
    for _ in range(5):
        localpart = "u-" + secrets.token_hex(4)
        displayname = _pseudonymous_displayname(localpart)
        try:
            result = client.register_user(localpart, password, displayname, admin=False)
        except MatrixError as exc:
            if exc.errcode == "M_USER_IN_USE":
                last_exc = exc
                continue  # collision on the random localpart — re-roll
            raise
        account = MatrixAccount.objects.create(
            user=user,
            matrix_user_id=result["user_id"],
            password=password,
            onboarding_status=MatrixAccount.Status.CREATED,
        )
        log.info("matrix: provisioned account %s for user %s", account.matrix_user_id, user.pk)
        return account

    raise MatrixError("Kein freier Matrix-Localpart nach mehreren Versuchen.") from last_exc


def ensure_room_for_list(list_obj) -> MatrixRoom:
    """Return the list's Matrix room, creating it on first use.

    Idempotent: a second call returns the existing MatrixRoom without touching
    Synapse. The room is invite-only with history visible only from the invite
    onward; the service account holds the sole invite right (PL 100). Broadcast
    lists raise events_default to 50 so only admins (promoted to PL≥50 in
    Phase 4) can post. Acting identity is the bootstrapped service account.
    """
    if not settings.MATRIX_ENABLED:
        raise MatrixError("Matrix-Integration ist deaktiviert (MATRIX_ENABLED=False).")

    existing = MatrixRoom.objects.filter(list=list_obj).first()
    if existing:
        return existing
    if not list_obj.matrix_room_enabled:
        raise MatrixError(f"Liste {list_obj.pk} ist nicht für einen Matrix-Raum freigeschaltet.")

    service_account = MatrixServiceAccount.get()
    if service_account is None:
        raise MatrixError(
            "Kein Matrix-Service-Konto vorhanden — matrix_bootstrap_service_account ausführen."
        )

    client = MatrixClient.from_settings()
    events_default = 50 if list_obj.matrix_broadcast_only else 0
    room_id = client.create_room(
        service_account.access_token,
        name=list_obj.title,
        topic="",
        events_default=events_default,
        history_visibility="invited",
    )
    room = MatrixRoom.objects.create(list=list_obj, room_id=room_id)
    log.info("matrix: created room %s for list %s", room_id, list_obj.pk)
    return room
