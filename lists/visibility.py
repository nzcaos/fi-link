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
