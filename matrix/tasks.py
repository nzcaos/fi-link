"""Procrastinate tasks owned by the matrix app.

Discovery: `procrastinate.contrib.django` auto-imports a `tasks` submodule from
every INSTALLED_APP, so this file only needs to exist. Account provisioning is
exposed as a task for the event-driven path (Phase 4: enqueue on list join) so
the Synapse call is retried on transient failure; the credentials view calls
`ensure_matrix_account` directly because it needs the result to display.
"""
from __future__ import annotations

import logging

from django.conf import settings
from procrastinate.contrib.django import app

log = logging.getLogger(__name__)


@app.periodic(cron="30 4 * * *")
@app.task(queueing_lock="matrix_reconcile_rooms", pass_context=False)
def reconcile_all_rooms(timestamp: int) -> None:
    """Daily drift repair: re-invite Benutzergruppe members who are missing from
    their class room (e.g. an invite that failed transiently and exhausted its
    retries). One bad room must not abort the sweep, so failures are logged
    per-list, not raised.
    """
    if not settings.MATRIX_ENABLED:
        return
    from lists.models import List

    from . import service

    lists = List.objects.filter(
        matrix_room_enabled=True, archived_at__isnull=True, matrix_room__isnull=False
    )
    for lst in lists:
        try:
            service.reconcile_list_room(lst)
        except Exception:  # pragma: no cover - defensive sweep guard
            log.exception("matrix: reconcile failed for list %s", lst.pk)


@app.task(name="matrix.provision_account", pass_context=False)
def provision_matrix_account(user_id: int) -> None:
    if not settings.MATRIX_ENABLED:
        return
    from accounts.models import User

    from .service import ensure_matrix_account

    user = User.objects.get(pk=user_id)
    ensure_matrix_account(user)


def _resolve(user_id: int, list_id: int):
    from accounts.models import User
    from lists.models import List

    return (
        User.objects.filter(pk=user_id).first(),
        List.objects.filter(pk=list_id).first(),
    )


@app.task(name="matrix.sync_membership_join", pass_context=False)
def sync_membership_join(user_id: int, list_id: int) -> None:
    if not settings.MATRIX_ENABLED:
        return
    from . import service

    user, lst = _resolve(user_id, list_id)
    if user and lst:
        service.sync_user_into_room(user, lst)


@app.task(name="matrix.sync_membership_leave", pass_context=False)
def sync_membership_leave(user_id: int, list_id: int) -> None:
    if not settings.MATRIX_ENABLED:
        return
    from . import service

    user, lst = _resolve(user_id, list_id)
    if user and lst:
        service.remove_user_from_room(user, lst)


@app.task(name="matrix.sync_admin_power", pass_context=False)
def sync_admin_power(user_id: int, list_id: int, is_admin: bool) -> None:
    if not settings.MATRIX_ENABLED:
        return
    from . import service

    user, lst = _resolve(user_id, list_id)
    if user and lst:
        service.sync_user_power(user, lst, is_admin)


@app.task(name="matrix.sync_room_name", pass_context=False)
def sync_room_name(list_id: int) -> None:
    if not settings.MATRIX_ENABLED:
        return
    from lists.models import List

    from . import service

    lst = List.objects.filter(pk=list_id).first()
    if lst:
        service.rename_room(lst)


@app.task(name="matrix.send_broadcast", pass_context=False)
def send_matrix_broadcast(list_id: int, body: str) -> None:
    """Programmatic / retryable send path. The admin UI sends inline for
    immediate feedback; this exists for callers that prefer fire-and-forget.
    """
    if not settings.MATRIX_ENABLED:
        return
    from lists.models import List

    from . import service

    lst = List.objects.filter(pk=list_id).first()
    if lst:
        service.send_to_list(lst, body)
