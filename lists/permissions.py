"""Permission helpers for the lists app.

Pure functions over the authoritative tables (`ListAdmin`, `ListAccess`,
`RecordManager`, `User.is_superuser`). Used by views and templates; no Django
`User.has_perm` machinery, no `django-guardian` — see CLAUDE.md / *Permission
and visibility layer* for the rationale.
"""
from __future__ import annotations

from django.db.models import Q, QuerySet

from .models import List, ListAccess, ListAdmin, ListRecord, RecordManager


def is_super_admin(user) -> bool:
    return bool(getattr(user, "is_authenticated", False) and user.is_superuser)


def can_user_admin_list(user, lst: List) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return ListAdmin.objects.filter(list=lst, user=user).exists()


def can_user_see_list(user, lst: List) -> bool:
    """A user "sees" a list if it is public, they are an admin, they are in
    the Benutzergruppe (ListAccess), or they are super-admin.

    Archived lists are still visible to admins/super-admin; regular members
    keep seeing them as read-only (the records are still useful for them).
    """
    if lst.visibility in (
        List.Visibility.PUBLIC_VISIBLE,
        List.Visibility.PUBLIC_EDITABLE,
    ):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    if ListAdmin.objects.filter(list=lst, user=user).exists():
        return True
    return ListAccess.objects.filter(list=lst, user=user).exists()


def can_user_create_top_level_list(user) -> bool:
    """Only the super-admin creates lists without a parent (Elternbeirat,
    Lehrerkollegium, …). Regular users always pick a parent.
    """
    return is_super_admin(user)


def can_user_create_sublist_under(user, parent: List) -> bool:
    """The creator of a sub-list must have admin or member status on the
    parent. Super-admin always qualifies.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    if parent.archived_at is not None:
        return False
    if ListAdmin.objects.filter(list=parent, user=user).exists():
        return True
    if ListAccess.objects.filter(list=parent, user=user).exists():
        return True
    if parent.visibility in (
        List.Visibility.PUBLIC_VISIBLE,
        List.Visibility.PUBLIC_EDITABLE,
    ):
        return True
    return False


def eligible_parents_for(user) -> QuerySet[List]:
    """The lists a `Liste anlegen`-form may offer as parent for this user.

    Super-admin sees every non-archived list. Regular users see lists where
    they are admin or member, plus public lists.
    """
    base = List.objects.filter(archived_at__isnull=True)
    if not getattr(user, "is_authenticated", False):
        return base.none()
    if user.is_superuser:
        return base
    return base.filter(
        Q(admins__user=user)
        | Q(members__user=user)
        | Q(visibility__in=[
            List.Visibility.PUBLIC_VISIBLE,
            List.Visibility.PUBLIC_EDITABLE,
        ])
    ).distinct()


def can_user_edit_record(user, record: ListRecord) -> bool:
    """A record is editable by its RecordManagers, by list admins of the
    containing list, by the subject's own User (even without an explicit
    RecordManager row — see CLAUDE.md / *Family-association model*: "a
    parent's record is editable by themselves once they self-register"),
    and by super-admin. Archived records are read-only.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if record.archived_at is not None:
        return False
    if user.is_superuser:
        return True
    if record.subject_id == getattr(user, "person_id", None):
        return True
    if RecordManager.objects.filter(record=record, user=user).exists():
        return True
    if ListAdmin.objects.filter(list=record.list, user=user).exists():
        return True
    return False
