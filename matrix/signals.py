"""Membership-sync signals (docs/matrix-implementation-plan.md, Phase 4).

The single sync primitive: react to ListAccess / ListAdmin / List changes and
enqueue a procrastinate task that mirrors the change into Matrix. Because every
join path funnels through `ListAccess.get_or_create` and self-removal through
`ListAccess.delete()` (and transfer/rollover re-use both), wiring these signals
covers join, leave, transfer and merge without touching the lists app.

Enqueue happens in `transaction.on_commit` so a rolled-back membership change
never schedules Matrix work, and the row is guaranteed to exist when the task
runs. Everything is a no-op unless MATRIX_ENABLED and the list is room-enabled.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from lists.models import List, ListAccess, ListAdmin

from . import tasks

log = logging.getLogger(__name__)


def _room_enabled(list_obj) -> bool:
    return bool(list_obj and list_obj.matrix_room_enabled)


def _list_for_deleted(instance) -> List | None:
    # On cascade-delete of the List itself the related row is already gone;
    # fetch defensively so the handler degrades to a no-op instead of raising.
    return List.objects.filter(pk=instance.list_id).first()


# Each handler checks MATRIX_ENABLED first so a Matrix-off deployment pays no
# extra query on the (frequent) membership-change path.


@receiver(post_save, sender=ListAccess, dispatch_uid="matrix_listaccess_save")
def on_listaccess_save(sender, instance, created, **kwargs):
    if not settings.MATRIX_ENABLED or not created or not _room_enabled(instance.list):
        return
    uid, lid = instance.user_id, instance.list_id
    transaction.on_commit(lambda: tasks.sync_membership_join.defer(user_id=uid, list_id=lid))


@receiver(post_delete, sender=ListAccess, dispatch_uid="matrix_listaccess_delete")
def on_listaccess_delete(sender, instance, **kwargs):
    if not settings.MATRIX_ENABLED or not _room_enabled(_list_for_deleted(instance)):
        return
    uid, lid = instance.user_id, instance.list_id
    transaction.on_commit(lambda: tasks.sync_membership_leave.defer(user_id=uid, list_id=lid))


@receiver(post_save, sender=ListAdmin, dispatch_uid="matrix_listadmin_save")
def on_listadmin_save(sender, instance, created, **kwargs):
    if not settings.MATRIX_ENABLED or not created or not _room_enabled(instance.list):
        return
    uid, lid = instance.user_id, instance.list_id
    transaction.on_commit(lambda: tasks.sync_admin_power.defer(user_id=uid, list_id=lid, is_admin=True))


@receiver(post_delete, sender=ListAdmin, dispatch_uid="matrix_listadmin_delete")
def on_listadmin_delete(sender, instance, **kwargs):
    if not settings.MATRIX_ENABLED or not _room_enabled(_list_for_deleted(instance)):
        return
    uid, lid = instance.user_id, instance.list_id
    transaction.on_commit(lambda: tasks.sync_admin_power.defer(user_id=uid, list_id=lid, is_admin=False))


@receiver(post_save, sender=List, dispatch_uid="matrix_list_rename")
def on_list_save(sender, instance, created, update_fields=None, **kwargs):
    # Keep the room name in sync on rollover renames (5a → 6a). Skip creation
    # (no room yet) and saves that demonstrably don't touch the title.
    if not settings.MATRIX_ENABLED or created or not _room_enabled(instance):
        return
    if update_fields is not None and "title" not in update_fields:
        return
    lid = instance.id
    transaction.on_commit(lambda: tasks.sync_room_name.defer(list_id=lid))
