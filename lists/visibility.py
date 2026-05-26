"""Per-field visibility for ListRecord values.

The audience primitive is a `List_ID` (NULL = public). A field is visible to
`user` if any of the following holds:

1. The attribute is `must_be_public` (template-enforced, owner cannot hide it).
2. `user` is super-admin.
3. `user` is a `RecordManager` of the record (owner/co-editor).
4. `user` is a `ListAdmin` of the record's containing list.
5. A `ListRecordAccess(record, attribute, audience=NULL)` row exists (public).
6. A `ListRecordAccess(record, attribute, audience=L)` row exists AND `user`
   is in `L`'s Benutzergruppe (member, admin, or `L` is a public list).

Archived records are visible only to managers/admins/super-admin.

See CLAUDE.md / *Permission and visibility layer* for why this is computed
from the authoritative tables rather than via `django-guardian`.
"""
from __future__ import annotations

from typing import Iterable

from .models import (
    List,
    ListAccess,
    ListAdmin,
    ListAttribute,
    ListRecord,
    ListRecordAccess,
    RecordManager,
)


def _user_is_in_benutzergruppe(user, audience_list: List) -> bool:
    if audience_list.visibility in (
        List.Visibility.PUBLIC_VISIBLE,
        List.Visibility.PUBLIC_EDITABLE,
    ):
        return True
    if ListAccess.objects.filter(list=audience_list, user=user).exists():
        return True
    if ListAdmin.objects.filter(list=audience_list, user=user).exists():
        return True
    return False


def can_user_see_field(user, record: ListRecord, attribute: ListAttribute) -> bool:
    if attribute.must_be_public:
        return True

    if not getattr(user, "is_authenticated", False):
        return False

    if user.is_superuser:
        return True

    # Subject's own User sees everything on their own record (CLAUDE.md /
    # *Family-association model*: "a parent's record is editable by themselves
    # once they self-register"). Implies visibility too.
    if record.subject_id == getattr(user, "person_id", None):
        return True

    if RecordManager.objects.filter(record=record, user=user).exists():
        return True

    if ListAdmin.objects.filter(list=record.list, user=user).exists():
        return True

    if record.archived_at is not None:
        return False

    access_rows = ListRecordAccess.objects.filter(record=record, attribute=attribute)

    if access_rows.filter(audience__isnull=True).exists():
        return True

    audience_ids = list(
        access_rows.filter(audience__isnull=False).values_list("audience_id", flat=True)
    )
    if not audience_ids:
        return False

    for audience in List.objects.filter(pk__in=audience_ids):
        if _user_is_in_benutzergruppe(user, audience):
            return True
    return False


def visible_attributes_for(user, record: ListRecord) -> Iterable[ListAttribute]:
    """Return the attributes of `record`'s template that `user` may see on
    this specific record, in template order. Used by the list-render.
    """
    for attribute in record.list.template.attributes.all():
        if can_user_see_field(user, record, attribute):
            yield attribute


def can_user_see_subject_name(user, record: ListRecord) -> bool:
    """M8: subject-name visibility — same audience semantics as
    `can_user_see_field`, but the matching `LIST_RECORD_ACCESS` rows have
    `attribute_id IS NULL`.

    Admin override: super-admin and list-admin of the containing list always
    see the real name (moderation needs it). The subject's own User and any
    RecordManager also always see it (their own data). For everyone else, the
    matrix is the source of truth. See CLAUDE.md / *Subject-name visibility*.
    """
    if not getattr(user, "is_authenticated", False):
        # Anonymous viewers (public-visibility lists) fall through to the
        # audience-row check below; only a (record, NULL, audience=NULL)
        # row can grant them visibility.
        return _public_subject_name_allowed(record)

    if user.is_superuser:
        return True

    if record.subject_id == getattr(user, "person_id", None):
        return True

    if RecordManager.objects.filter(record=record, user=user).exists():
        return True

    if ListAdmin.objects.filter(list=record.list, user=user).exists():
        return True

    if record.archived_at is not None:
        return False

    access_rows = ListRecordAccess.objects.filter(record=record, attribute__isnull=True)

    if access_rows.filter(audience__isnull=True).exists():
        return True

    audience_ids = list(
        access_rows.filter(audience__isnull=False).values_list("audience_id", flat=True)
    )
    if not audience_ids:
        return False

    for audience in List.objects.filter(pk__in=audience_ids):
        if _user_is_in_benutzergruppe(user, audience):
            return True
    return False


def _public_subject_name_allowed(record: ListRecord) -> bool:
    """Helper for anonymous viewers: only an explicit (record, NULL,
    audience=NULL) row grants visibility — list-public visibility on
    audience lists does not propagate to unauthenticated callers because
    the per-list `can_user_see_list` check is the outer gate, and at the
    point this helper is reached, the caller already passed that gate
    against a publicly visible list. The name still requires its own opt-in.
    """
    return ListRecordAccess.objects.filter(
        record=record, attribute__isnull=True, audience__isnull=True
    ).exists()
