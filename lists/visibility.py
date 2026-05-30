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

from collections import defaultdict
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


def build_visible_rows(user, lst: List) -> list[dict]:
    """Render-ready rows for `lst`'s active records as seen by `user`.

    Each row is ``{"record", "subject_display", "fields"}`` where ``fields`` is
    a list of ``(attribute, value)`` pairs limited to the attributes `user` may
    see, and ``subject_display`` is the subject's name or a per-render ``?N``
    anonymisation placeholder (M8). Shared by the list-detail page and the
    dynamic list part of a Form so both honour the exact same visibility rules.

    N12: this is the hot path (one render = N records × M attributes). It must
    not delegate to the per-field/per-name helpers, which each issue several
    queries — that is O(N×M) DB round-trips. Instead every table the helpers
    consult is loaded once up front and the same visibility logic is then
    evaluated in memory. The single-record helpers above keep the readable
    reference semantics for callers that look at one record (record_edit etc.);
    this function is their bulk-resolved equivalent and the two must stay in
    sync.
    """
    records = list(
        lst.records.filter(archived_at__isnull=True)
        .select_related("subject")
        .prefetch_related("values")
    )
    attributes = list(lst.template.attributes.all())
    if not records:
        return []

    record_ids = [r.pk for r in records]

    # One query for every (record, attribute|sentinel, audience) grant. A real
    # attribute row has attribute_id set + sentinel NULL; the two subject-level
    # axes both have attribute_id NULL and are disambiguated by `sentinel`
    # ("name" / "email"). audience_id IS NULL is the public sentinel. The key
    # therefore carries the sentinel so the name and email axes don't collapse.
    audiences_by_key: dict[tuple[int, int | None, str | None], set[int | None]] = defaultdict(set)
    for rec_id, attr_id, sentinel, aud_id in ListRecordAccess.objects.filter(
        record_id__in=record_ids
    ).values_list("record_id", "attribute_id", "sentinel", "audience_id"):
        audiences_by_key[(rec_id, attr_id, sentinel)].add(aud_id)

    authed = bool(getattr(user, "is_authenticated", False))
    is_super = authed and user.is_superuser
    person_id = getattr(user, "person_id", None)

    managed_record_ids: set[int] = set()
    is_list_admin = False
    visible_audience_ids: set[int] = set()
    if authed and not is_super:
        managed_record_ids = set(
            RecordManager.objects.filter(
                user=user, record_id__in=record_ids
            ).values_list("record_id", flat=True)
        )
        is_list_admin = ListAdmin.objects.filter(list=lst, user=user).exists()

        referenced_audience_ids = {
            aid
            for auds in audiences_by_key.values()
            for aid in auds
            if aid is not None
        }
        if referenced_audience_ids:
            public_audiences = set(
                List.objects.filter(
                    pk__in=referenced_audience_ids,
                    visibility__in=(
                        List.Visibility.PUBLIC_VISIBLE,
                        List.Visibility.PUBLIC_EDITABLE,
                    ),
                ).values_list("pk", flat=True)
            )
            accessed = set(
                ListAccess.objects.filter(
                    user=user, list_id__in=referenced_audience_ids
                ).values_list("list_id", flat=True)
            )
            admined = set(
                ListAdmin.objects.filter(
                    user=user, list_id__in=referenced_audience_ids
                ).values_list("list_id", flat=True)
            )
            visible_audience_ids = public_audiences | accessed | admined

    def _audience_grants(key: tuple[int, int | None, str | None]) -> bool:
        auds = audiences_by_key.get(key)
        if not auds:
            return False
        if None in auds:  # public sentinel
            return True
        return bool(auds & visible_audience_ids)

    def _field_visible(record: ListRecord, attribute: ListAttribute) -> bool:
        if attribute.must_be_public:
            return True
        if not authed:
            return False
        if is_super:
            return True
        if record.subject_id == person_id:
            return True
        if record.pk in managed_record_ids:
            return True
        if is_list_admin:
            return True
        return _audience_grants((record.pk, attribute.pk, None))

    def _name_visible(record: ListRecord) -> bool:
        key = (record.pk, None, ListRecordAccess.Sentinel.NAME)
        if not authed:
            # Anonymous viewers see the name only via an explicit public row.
            return None in audiences_by_key.get(key, set())
        if is_super:
            return True
        if record.subject_id == person_id:
            return True
        if record.pk in managed_record_ids:
            return True
        if is_list_admin:
            return True
        return _audience_grants(key)

    def _email_visible(record: ListRecord) -> bool:
        # Multi-person row: visibility of the subject PERSON's account email.
        # No public default row is ever written (opt-in / starts hidden); the
        # override hierarchy mirrors the name axis for a consistent moderation
        # surface. See CLAUDE.md / *Family-association model / Multi-person row*.
        key = (record.pk, None, ListRecordAccess.Sentinel.EMAIL)
        if not authed:
            return None in audiences_by_key.get(key, set())
        if is_super:
            return True
        if record.subject_id == person_id:
            return True
        if record.pk in managed_record_ids:
            return True
        if is_list_admin:
            return True
        return _audience_grants(key)

    rows: list[dict] = []
    anon_counter = 0
    for record in records:
        fields = []
        values_map = {v.attribute_id: v.value for v in record.values.all()}
        for attribute in attributes:
            if _field_visible(record, attribute):
                fields.append((attribute, values_map.get(attribute.pk, "")))
        if _name_visible(record):
            subject_display = str(record.subject)
        else:
            anon_counter += 1
            subject_display = f"?{anon_counter}"
        # subject_email: the PERSON's account email, but only when the email
        # sentinel grants it to this viewer (else None). The flat list-detail
        # render ignores it today; the multi-person row composer (Phase 4)
        # consumes it for the parent columns.
        subject_email = record.subject.email if _email_visible(record) else None
        rows.append(
            {
                "record": record,
                "subject_display": subject_display,
                "subject_email": subject_email,
                "fields": fields,
            }
        )
    return rows


def _can_user_see_subject_sentinel(user, record: ListRecord, sentinel: str) -> bool:
    """Shared logic for the two subject-level sentinels — same audience
    semantics as `can_user_see_field`, but the matching `LIST_RECORD_ACCESS`
    rows have `attribute_id IS NULL` and the given `sentinel`.

    Admin override: super-admin and list-admin of the containing list always
    see it (moderation needs it); so do the subject's own User and any
    RecordManager (their own data). For everyone else the matrix is the source
    of truth. The name sentinel has a public default row written on record
    creation; the email sentinel has none (opt-in / starts hidden).
    """
    if not getattr(user, "is_authenticated", False):
        # Anonymous viewers (public-visibility lists) see it only via an
        # explicit (record, NULL, sentinel, audience=NULL) public row.
        return ListRecordAccess.objects.filter(
            record=record,
            attribute__isnull=True,
            sentinel=sentinel,
            audience__isnull=True,
        ).exists()

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

    access_rows = ListRecordAccess.objects.filter(
        record=record, attribute__isnull=True, sentinel=sentinel
    )

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


def can_user_see_subject_name(user, record: ListRecord) -> bool:
    """M8: visibility of the subject's name. See CLAUDE.md /
    *Subject-name visibility*."""
    return _can_user_see_subject_sentinel(
        user, record, ListRecordAccess.Sentinel.NAME
    )


def can_user_see_subject_email(user, record: ListRecord) -> bool:
    """Multi-person row: visibility of the subject PERSON's account email
    (`PERSON.email`). Same audience semantics as the name sentinel, but with
    `sentinel="email"`, no must-be-public path and no default public row — the
    email starts hidden and is disclosed per audience opt-in. See CLAUDE.md /
    *Family-association model / Multi-person row*."""
    return _can_user_see_subject_sentinel(
        user, record, ListRecordAccess.Sentinel.EMAIL
    )
