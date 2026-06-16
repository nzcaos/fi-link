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


# ---------------------------------------------------------------------------
# Membership sync (Phase 4) — orchestration called from the procrastinate tasks
# that the ListAccess / ListAdmin / List signals enqueue.
# ---------------------------------------------------------------------------


def _service_token() -> str:
    svc = MatrixServiceAccount.get()
    if svc is None:
        raise MatrixError("Kein Matrix-Service-Konto vorhanden — matrix_bootstrap_service_account.")
    return svc.access_token


def _is_list_admin(user, list_obj) -> bool:
    from lists.models import ListAdmin

    return ListAdmin.objects.filter(list=list_obj, user=user).exists()


def _tolerate_logical(exc: MatrixError, what: str) -> None:
    """A 4xx with an errcode is a logical state (already in room, not in room,
    …) — log and move on. A transport error (no errcode) is transient, so it
    re-raises and the procrastinate task retries it.
    """
    if exc.errcode is None:
        raise exc
    log.info("matrix: %s skipped (%s)", what, exc)


def sync_user_into_room(user, list_obj) -> None:
    """Ensure the user's account + the list's room exist, invite them, and set
    their power level (admin → 50, member stays at users_default 0). Idempotent.
    """
    account = ensure_matrix_account(user)
    room = ensure_room_for_list(list_obj)
    client = MatrixClient.from_settings()
    token = _service_token()
    try:
        client.invite(token, room.room_id, account.matrix_user_id)
    except MatrixError as exc:
        _tolerate_logical(exc, f"invite {account.matrix_user_id} to {room.room_id}")
    if _is_list_admin(user, list_obj):
        client.set_user_power_level(token, room.room_id, account.matrix_user_id, 50)
    if account.onboarding_status != MatrixAccount.Status.INVITED:
        account.onboarding_status = MatrixAccount.Status.INVITED
        account.save(update_fields=["onboarding_status", "updated_at"])


def remove_user_from_room(user, list_obj) -> None:
    """Kick the user from the list's room (on self-removal / list leave)."""
    account = MatrixAccount.objects.filter(user=user).first()
    room = MatrixRoom.objects.filter(list=list_obj).first()
    if not account or not room:
        return
    client = MatrixClient.from_settings()
    try:
        client.kick(_service_token(), room.room_id, account.matrix_user_id, reason="aus Liste entfernt")
    except MatrixError as exc:
        _tolerate_logical(exc, f"kick {account.matrix_user_id} from {room.room_id}")


def sync_user_power(user, list_obj, is_admin: bool) -> None:
    """Promote/demote a user's power level (list-admin change → 50 / 0)."""
    account = MatrixAccount.objects.filter(user=user).first()
    room = MatrixRoom.objects.filter(list=list_obj).first()
    if not account or not room:
        return
    client = MatrixClient.from_settings()
    client.set_user_power_level(_service_token(), room.room_id, account.matrix_user_id, 50 if is_admin else 0)


def rename_room(list_obj) -> None:
    """Sync the room name to the list title (rollover renames 5a → 6a)."""
    room = MatrixRoom.objects.filter(list=list_obj).first()
    if not room:
        return
    client = MatrixClient.from_settings()
    client.set_room_name(_service_token(), room.room_id, list_obj.title)


def send_to_list(list_obj, body: str) -> str:
    """Post a message to the list's room as the service account (PL 100, so it
    sends in chat *and* broadcast mode). Creates the room if eligible and not
    yet present. Returns the event id.
    """
    if not settings.MATRIX_ENABLED:
        raise MatrixError("Matrix-Integration ist deaktiviert (MATRIX_ENABLED=False).")
    room = MatrixRoom.objects.filter(list=list_obj).first()
    if room is None:
        room = ensure_room_for_list(list_obj)
    client = MatrixClient.from_settings()
    return client.send_message(_service_token(), room.room_id, body)
