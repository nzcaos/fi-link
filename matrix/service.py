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

    from django.db import transaction

    from accounts.models import User

    with transaction.atomic():
        # Lock the user row so a double-clicked opt-in can't register two
        # Synapse accounts (the second of which no local row would point to).
        User.objects.select_for_update().filter(pk=user.pk).first()
        existing = MatrixAccount.objects.filter(user=user).first()
        if existing:
            return existing
        return _register_matrix_account(user)


def _register_matrix_account(user) -> MatrixAccount:
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
    """Ensure the list's room exists, invite the user, and set their power
    level (admin → 50, member stays at users_default 0). Idempotent.

    Opt-in: a user WITHOUT a MatrixAccount is skipped — accounts are created
    only when the user sets up their Messenger-Zugang themselves (views.
    messenger_access), never as a side effect of joining a list. That view
    then calls this for each of their room-enabled lists.
    """
    if not settings.MATRIX_ENABLED:
        raise MatrixError("Matrix-Integration ist deaktiviert (MATRIX_ENABLED=False).")
    account = MatrixAccount.objects.filter(user=user).first()
    if account is None:
        return
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
    """Promote/demote a user's power level (list-admin change → 50 / 0).

    On *promotion* the new admin may not be in the list's Benutzergruppe at all:
    an ADMIN_INVITE_TOKEN handover (lists.views.admin_invite_accept) creates a
    `ListAdmin` row but no `ListAccess`, so they were never invited, and a bare
    `set_user_power_level` would set power for someone who isn't in the room.
    Promotion therefore routes through `sync_user_into_room`, which invites
    them and (because the `ListAdmin` row now exists) sets power to 50 — or
    skips them if they have not opted in (no MatrixAccount); opting in later
    runs the same path via `room_lists_for_user`, which includes admin lists.

    *Demotion* only lowers the power level — the user may still be a plain member
    via `ListAccess`, so they are never kicked here (room-leave is the
    `ListAccess`-delete path).
    """
    if is_admin:
        sync_user_into_room(user, list_obj)
        return
    account = MatrixAccount.objects.filter(user=user).first()
    room = MatrixRoom.objects.filter(list=list_obj).first()
    if not account or not room:
        return
    client = MatrixClient.from_settings()
    client.set_user_power_level(_service_token(), room.room_id, account.matrix_user_id, 0)


def rename_room(list_obj) -> None:
    """Sync the room name to the list title (rollover renames 5a → 6a)."""
    room = MatrixRoom.objects.filter(list=list_obj).first()
    if not room:
        return
    client = MatrixClient.from_settings()
    client.set_room_name(_service_token(), room.room_id, list_obj.title)


def ban_user_from_list(user, list_obj, reason: str = "") -> None:
    """Emergency: ban a member's Matrix account from the list's room (they can
    no longer rejoin until unbanned). No-op if no account/room.
    """
    account = MatrixAccount.objects.filter(user=user).first()
    room = MatrixRoom.objects.filter(list=list_obj).first()
    if not account or not room:
        return
    client = MatrixClient.from_settings()
    client.ban(_service_token(), room.room_id, account.matrix_user_id, reason=reason)


def unban_user_from_list(user, list_obj) -> None:
    """Lift a ban so the user can be (re-)invited."""
    account = MatrixAccount.objects.filter(user=user).first()
    room = MatrixRoom.objects.filter(list=list_obj).first()
    if not account or not room:
        return
    client = MatrixClient.from_settings()
    try:
        client.unban(_service_token(), room.room_id, account.matrix_user_id)
    except MatrixError as exc:
        _tolerate_logical(exc, f"unban {account.matrix_user_id} from {room.room_id}")


def room_membership_for_list(list_obj) -> dict[str, str]:
    """Map matrix_user_id → membership for the list's room (for the moderation
    UI). Empty if the list has no room yet.
    """
    room = MatrixRoom.objects.filter(list=list_obj).first()
    if room is None:
        return {}
    client = MatrixClient.from_settings()
    return client.room_memberships(_service_token(), room.room_id)


def reconcile_list_room(list_obj) -> int:
    """Repair membership drift: invite every Benutzergruppe member (USER with a
    Matrix account) who is not currently joined/invited to the room. Returns the
    number of repair invites issued. Extra room members are deliberately NOT
    kicked (could be admins or the service account); only missing ones are added.
    """
    from django.db.models import Q

    from lists.models import ListAccess, ListAdmin

    room = MatrixRoom.objects.filter(list=list_obj).first()
    if room is None:
        return 0
    client = MatrixClient.from_settings()
    token = _service_token()
    present = client.room_member_ids(token, room.room_id)

    # Expected room members are the Benutzergruppe (ListAccess) *plus* the list's
    # admins — a handover admin may hold a ListAdmin row without ListAccess (see
    # sync_user_power), and must still be repaired into the room if their
    # promotion-time invite failed transiently.
    expected = (
        MatrixAccount.objects.filter(
            Q(user__in=ListAccess.objects.filter(list=list_obj).values("user"))
            | Q(user__in=ListAdmin.objects.filter(list=list_obj).values("user"))
        ).values_list("matrix_user_id", flat=True)
    )
    repaired = 0
    for mxid in expected:
        if mxid in present:
            continue
        try:
            client.invite(token, room.room_id, mxid)
            repaired += 1
        except MatrixError as exc:
            _tolerate_logical(exc, f"reconcile invite {mxid} to {room.room_id}")
    if repaired:
        log.info("matrix: reconcile list %s issued %d repair invites", list_obj.pk, repaired)
    return repaired


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


# ---------------------------------------------------------------------------
# Opt-in and erasure
# ---------------------------------------------------------------------------


def room_lists_for_user(user):
    """Active room-enabled lists the user belongs to (Benutzergruppe or admin)
    — the rooms they are invited to once they opt in."""
    from django.db.models import Q

    from lists.models import List

    return List.objects.filter(
        Q(members__user=user) | Q(admins__user=user),
        matrix_room_enabled=True,
        archived_at__isnull=True,
    ).distinct()


# How long erasure waits for Synapse's background redaction before giving up
# (and aborting, so nothing is deactivated with messages still visible).
REDACT_WAIT_SECONDS = 120


def _wait_for_redaction(client: MatrixClient, token: str, redact_id: str) -> None:
    import time

    deadline = time.monotonic() + REDACT_WAIT_SECONDS
    while True:
        status = client.redact_status(token, redact_id)
        state = status.get("status")
        if state == "complete":
            failed = status.get("failed_redactions") or {}
            if failed:
                log.warning("matrix: %d redaction(s) failed in job %s", len(failed), redact_id)
            return
        if state == "failed":
            raise MatrixError(f"Synapse-Redaktion {redact_id} fehlgeschlagen.")
        if time.monotonic() > deadline:
            raise MatrixError(f"Synapse-Redaktion {redact_id} nach {REDACT_WAIT_SECONDS}s nicht fertig.")
        time.sleep(1)


def erase_matrix_account(account: MatrixAccount) -> None:
    """Remove a user's Matrix footprint on Synapse, then drop the local row.

    1. Class rooms: the service account (member since creation, redact PL)
       redacts the user's messages itself — this also reaches rooms the user
       already left (list leave = kick), where the user could no longer act.
    2. Every other room the user is still in (e.g. direct chats): Synapse's
       admin redaction, which puppets the user and so must FINISH while they
       are still a member — we poll its status before going on.
    3. Deactivate with erase=True (leaves all rooms, removes the profile).

    A 404 on the account means it is already gone on Synapse and counts as
    done; anything else raises, so the caller keeps track of a live account.
    """
    client = MatrixClient.from_settings()
    token = _service_token()
    mxid = account.matrix_user_id
    for room in MatrixRoom.objects.all():
        try:
            event_ids = client.sender_event_ids(token, room.room_id, mxid)
        except MatrixError as exc:
            if exc.status in (403, 404):  # room gone / service account not in it
                continue
            raise
        for event_id in event_ids:
            client.redact_event(token, room.room_id, event_id, reason="Konto gelöscht")
    try:
        redact_id = client.redact_user_events(token, mxid)
        if redact_id:
            _wait_for_redaction(client, token, redact_id)
        client.deactivate_user(token, mxid, erase=True)
    except MatrixError as exc:
        if exc.status != 404:
            raise
        log.info("matrix: %s not found on Synapse — treated as erased", mxid)
    log.info("matrix: erased account %s (user %s)", mxid, account.user_id)
    account.delete()


def delete_room_for_list(list_obj) -> None:
    """Shut down and purge the list's room on Synapse, then drop the local
    MatrixRoom row. No-op without a room. A 404 counts as already deleted."""
    room = MatrixRoom.objects.filter(list=list_obj).first()
    if room is None:
        return
    client = MatrixClient.from_settings()
    try:
        client.delete_room(_service_token(), room.room_id)
    except MatrixError as exc:
        if exc.status != 404:
            raise
    log.info("matrix: deleted room %s of list %s", room.room_id, list_obj.pk)
    room.delete()
