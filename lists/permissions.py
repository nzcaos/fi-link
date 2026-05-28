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


def candidate_invite_persons(inviting_user, target_list: List):
    """Persons that may be picked as `target_person` in a `ListInviteToken`
    created by `inviting_user` for `target_list`.

    Privacy goal: the inviter must not see Persons they do not already know
    of through their own list membership. Concretely, a Person `p` is offered
    iff `p` is the Subject of at least one active LIST_RECORD in a list that
    the inviter can see — which is:

    - any list where the inviter is admin or member,
    - any public list,
    - the target list itself,
    - the target list's parent (if any),
    - the target list's direct children (non-archived).

    Only persons that have a USER **and** a non-empty email are returned —
    the existing-USER invitation path needs both. Super-admin sees every
    USER-Person system-wide.

    Returns a Person queryset (caller may apply further ordering / limits).
    """
    # Imported here to keep the lists app permission module independent of
    # accounts at import time (avoids circular-import surprises in admin/
    # migrations).
    from accounts.models import Person

    base = (
        Person.objects.filter(user__isnull=False)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
    )
    if not getattr(inviting_user, "is_authenticated", False):
        return base.none()
    if inviting_user.is_superuser:
        return base.order_by("family_name", "given_name")

    visible_list_ids = set(
        eligible_parents_for(inviting_user).values_list("pk", flat=True)
    )
    visible_list_ids.add(target_list.pk)
    if target_list.parent_id:
        visible_list_ids.add(target_list.parent_id)
    visible_list_ids.update(
        target_list.children.filter(archived_at__isnull=True).values_list(
            "pk", flat=True
        )
    )
    return (
        base.filter(
            records__list_id__in=visible_list_ids,
            records__archived_at__isnull=True,
        )
        .distinct()
        .order_by("family_name", "given_name")
    )


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


# ---------------------------------------------------------------------------
# Lifecycle (Phase 6): transfers, self-removal, admin handover
# ---------------------------------------------------------------------------


def can_user_initiate_transfer(user, record: ListRecord) -> bool:
    """Who may request a class transfer for a record (CLAUDE.md / *Class
    transfer*): the source-list admin, a RecordManager of the moving Person,
    the Person's own User, or super-admin. Archived records can't be moved.
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
    return ListAdmin.objects.filter(list=record.list, user=user).exists()


def can_user_decide_transfer(user, transfer) -> bool:
    """Only an admin of the *destination* list (or super-admin) accepts/rejects
    a pending transfer — destination consent is the whole point of the two-step
    flow.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return ListAdmin.objects.filter(list=transfer.to_list, user=user).exists()


def would_self_removal_leave_no_admin(user, lst: List) -> bool:
    """True iff `user` is the *only* admin of `lst`. Used to block a self-
    removal / handover that would orphan the list (CLAUDE.md / *Admin
    handover*: "must add a successor first"). A non-admin leaving never drops
    the admin count, so this is False for them.
    """
    admin_ids = set(ListAdmin.objects.filter(list=lst).values_list("user_id", flat=True))
    if getattr(user, "id", None) not in admin_ids:
        return False
    return len(admin_ids) <= 1


def can_user_edit_record_visibility(user, record: ListRecord) -> bool:
    """B3: who may write the per-(record, attribute, audience) visibility
    matrix. Only the subject's own User, explicit RecordManagers, and the
    super-admin. Pure ListAdmins do **not** qualify — they may edit values
    via `can_user_edit_record` for moderation, but visibility belongs to
    the data subject. See CLAUDE.md / *Who may edit the visibility matrix*.
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
    return False
