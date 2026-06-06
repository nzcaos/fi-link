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
    PersonRelationship,
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


class _VisibilityContext:
    """Bulk-resolved, in-memory visibility predicates for one render.

    N12: rendering a list is the hot path (N records × M attributes). The
    single-record helpers above each issue several queries; calling them per
    field would be O(N×M) round-trips. Instead every table they consult is
    loaded once up front and the same logic is evaluated in memory here. The
    flat (`build_visible_rows`) and wide (`build_composed_rows`) builders both
    drive off this one context, so the two render paths can never drift in how
    they decide visibility. The reference helpers above and these predicates
    must stay in sync.
    """

    def __init__(self, user, lst: List, records: list[ListRecord]):
        record_ids = [r.pk for r in records]

        # One query for every (record, attribute|sentinel, audience) grant. A
        # real attribute row has attribute_id set + sentinel NULL; the two
        # subject-level axes both have attribute_id NULL and are disambiguated
        # by `sentinel` ("name" / "email"). audience_id IS NULL is the public
        # sentinel. The key carries the sentinel so name and email don't collapse.
        self._audiences_by_key: dict[tuple[int, int | None, str | None], set[int | None]] = defaultdict(set)
        for rec_id, attr_id, sentinel, aud_id in ListRecordAccess.objects.filter(
            record_id__in=record_ids
        ).values_list("record_id", "attribute_id", "sentinel", "audience_id"):
            self._audiences_by_key[(rec_id, attr_id, sentinel)].add(aud_id)

        self._authed = bool(getattr(user, "is_authenticated", False))
        self._is_super = self._authed and user.is_superuser
        self._person_id = getattr(user, "person_id", None)

        self._managed_record_ids: set[int] = set()
        self._is_list_admin = False
        self._visible_audience_ids: set[int] = set()
        if self._authed and not self._is_super:
            self._managed_record_ids = set(
                RecordManager.objects.filter(
                    user=user, record_id__in=record_ids
                ).values_list("record_id", flat=True)
            )
            self._is_list_admin = ListAdmin.objects.filter(list=lst, user=user).exists()

            referenced_audience_ids = {
                aid
                for auds in self._audiences_by_key.values()
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
                self._visible_audience_ids = public_audiences | accessed | admined

    def _audience_grants(self, key: tuple[int, int | None, str | None]) -> bool:
        auds = self._audiences_by_key.get(key)
        if not auds:
            return False
        if None in auds:  # public sentinel
            return True
        return bool(auds & self._visible_audience_ids)

    def _privileged(self, record: ListRecord) -> bool:
        # super-admin, the subject's own User, a RecordManager, or a list-admin
        # all see everything on the record (moderation / ownership).
        return (
            self._is_super
            or record.subject_id == self._person_id
            or record.pk in self._managed_record_ids
            or self._is_list_admin
        )

    def field_visible(self, record: ListRecord, attribute: ListAttribute) -> bool:
        if attribute.must_be_public:
            return True
        if not self._authed:
            return False
        if self._privileged(record):
            return True
        return self._audience_grants((record.pk, attribute.pk, None))

    def name_visible(self, record: ListRecord) -> bool:
        key = (record.pk, None, ListRecordAccess.Sentinel.NAME)
        if not self._authed:
            # Anonymous viewers see the name only via an explicit public row.
            return None in self._audiences_by_key.get(key, set())
        if self._privileged(record):
            return True
        return self._audience_grants(key)

    def email_visible(self, record: ListRecord) -> bool:
        # Multi-person row: visibility of the subject PERSON's account email.
        # No public default row is ever written (opt-in / starts hidden); the
        # override hierarchy mirrors the name axis for a consistent moderation
        # surface. See CLAUDE.md / *Family-association model / Multi-person row*.
        key = (record.pk, None, ListRecordAccess.Sentinel.EMAIL)
        if not self._authed:
            return None in self._audiences_by_key.get(key, set())
        if self._privileged(record):
            return True
        return self._audience_grants(key)


def build_visible_rows(user, lst: List) -> list[dict]:
    """Render-ready flat rows for `lst`'s active records as seen by `user`.

    Each row is ``{"record", "subject_display", "subject_email", "fields"}``
    where ``fields`` is a list of ``(attribute, value)`` pairs limited to the
    attributes `user` may see, and ``subject_display`` is the subject's name or
    a per-render ``?N`` anonymisation placeholder (M8). One row per record —
    used by `self`-mode lists. The wide multi-person view for `via_associate`
    lists is `build_composed_rows`. Shared by the list-detail page and the
    dynamic list part of a Form so both honour the exact same visibility rules.
    """
    records = list(
        lst.records.filter(archived_at__isnull=True)
        .select_related("subject")
        .prefetch_related("values")
    )
    attributes = list(lst.template.attributes.all())
    if not records:
        return []

    ctx = _VisibilityContext(user, lst, records)

    rows: list[dict] = []
    anon_counter = 0
    for record in records:
        fields = []
        values_map = {v.attribute_id: v.value for v in record.values.all()}
        for attribute in attributes:
            if ctx.field_visible(record, attribute):
                fields.append((attribute, values_map.get(attribute.pk, "")))
        if ctx.name_visible(record):
            subject_display = str(record.subject)
        else:
            anon_counter += 1
            subject_display = f"?{anon_counter}"
        # subject_email: the PERSON's account email, but only when the email
        # sentinel grants it to this viewer (else None).
        subject_email = record.subject.email if ctx.email_visible(record) else None
        rows.append(
            {
                "record": record,
                "subject_display": subject_display,
                "subject_email": subject_email,
                "fields": fields,
            }
        )
    return rows


def _role_label(role: str) -> str:
    """Derive a short column label for a parent cell from a PersonRelationship
    role: ``"Mutter von"`` → ``"Mutter"``. Falls back to the full role string
    when it doesn't end in `" von"`. A future per-role `display_label`
    (CLAUDE.md / *Multi-person row*) can override this convention later.
    """
    role = (role or "").strip()
    suffix = " von"
    if role.endswith(suffix):
        return role[: -len(suffix)].strip() or role
    return role


def build_composed_rows(user, lst: List) -> list[dict]:
    """Wide multi-person rows for `via_associate` lists — the school-class view
    that reproduces the paper list: one row per `member` record (the child)
    carrying the child's name + member-role attributes, followed by one cell
    group per linked associate (parent). See CLAUDE.md / *Family-association
    model / Multi-person row*.

    Parents are their own `associate` records, gathered via `PersonRelationship`
    and grouped by relationship role — **not** flattened onto the child. Each
    parent cell is rendered with **that parent record's own** name / field /
    email visibility (consent is per parent), so separated parents, a single
    guardian, and siblings all fall out without special-casing.

    The child's member attributes are laid out as a grid beside the name —
    columns side by side, distributed over one or more rows by each attribute's
    `display_row` (configurable in the LISTTEMPLATE). `member_grid` is that
    grid: one entry per display-row, each a list of ``{"attr", "value"}`` cells
    (``value`` is ``None`` where the field is hidden → rendered as "—"), so the
    label is shown once above each column instead of inline per value.

    Each row is::

        {"record", "subject_display", "fields", "member_grid": [
            [{"attr", "value"}, ...], ...], "parents": [
            {"role", "label", "record", "subject_display",
             "subject_email", "fields"}, ...]}
    """
    records = list(
        lst.records.filter(archived_at__isnull=True)
        .select_related("subject")
        .prefetch_related("values")
    )
    if not records:
        return []

    attributes = list(lst.template.attributes.all())
    member_attrs = [
        a for a in attributes if a.applies_to_role == ListAttribute.AppliesTo.MEMBER
    ]
    associate_attrs = [
        a for a in attributes if a.applies_to_role == ListAttribute.AppliesTo.ASSOCIATE
    ]

    # Member attributes grouped into display-rows. `member_attrs` is already
    # ordered by (position, id), so columns within a row keep that order; the
    # rows themselves are ordered by `display_row` number (gaps collapse).
    _member_rows_map: dict[int, list[ListAttribute]] = defaultdict(list)
    for a in member_attrs:
        _member_rows_map[a.display_row].append(a)
    member_display_rows = [_member_rows_map[k] for k in sorted(_member_rows_map)]

    ctx = _VisibilityContext(user, lst, records)

    member_records = [r for r in records if r.role == ListRecord.Role.MEMBER]
    associate_by_person: dict[int, ListRecord] = {
        r.subject_id: r for r in records if r.role == ListRecord.Role.ASSOCIATE
    }

    # child PERSON → ordered [(role, parent_person_id)] via PersonRelationship,
    # restricted to associates that have a record in THIS list. One query.
    rels_by_child: dict[int, list[tuple[str, int]]] = defaultdict(list)
    if member_records and associate_by_person:
        rel_rows = (
            PersonRelationship.objects.filter(
                subject_person_id__in=[r.subject_id for r in member_records],
                related_person_id__in=list(associate_by_person.keys()),
            )
            .order_by("role", "id")
            .values_list("subject_person_id", "related_person_id", "role")
        )
        for child_id, parent_id, role in rel_rows:
            rels_by_child[child_id].append((role, parent_id))

    anon_counter = 0

    def _cell(record: ListRecord, attrs: list[ListAttribute]):
        """Resolve one person's (child or parent) display name + visible
        attribute fields. Mutates the shared `?N` counter so anonymous people
        are numbered in render order across the whole table (M8: not stable).

        Returns `(subject_display, fields, cells)`:
        - `fields` — only the visible (attribute, value) pairs (stacked layout).
        - `cells` — one entry per attribute in `attrs` order, `None` where the
          field is hidden. Positional, so several people's cells line up under
          shared column headers in the wide class-list table.
        """
        nonlocal anon_counter
        values_map = {v.attribute_id: v.value for v in record.values.all()}
        fields = []
        cells = []
        for attribute in attrs:
            if ctx.field_visible(record, attribute):
                value = values_map.get(attribute.pk, "")
                fields.append((attribute, value))
                cells.append(value)
            else:
                cells.append(None)
        if ctx.name_visible(record):
            subject_display = str(record.subject)
        else:
            anon_counter += 1
            subject_display = f"?{anon_counter}"
        return subject_display, fields, cells

    rows: list[dict] = []
    for child in member_records:
        subject_display, fields, member_cells = _cell(child, member_attrs)
        # Map each member attribute to its (possibly hidden → None) value, then
        # shape the configured display-rows grid for the template.
        value_by_attr = dict(zip(member_attrs, member_cells))
        member_grid = [
            [{"attr": a, "value": value_by_attr[a]} for a in grp]
            for grp in member_display_rows
        ]
        parents = []
        for role, parent_id in rels_by_child.get(child.subject_id, []):
            prec = associate_by_person.get(parent_id)
            if prec is None:
                continue
            p_display, p_fields, p_cells = _cell(prec, associate_attrs)
            parents.append(
                {
                    "role": role,
                    "label": _role_label(role),
                    "record": prec,
                    "subject_display": p_display,
                    # parent email = PERSON.email, gated by the parent record's
                    # own email sentinel (opt-in, defaults hidden).
                    "subject_email": prec.subject.email if ctx.email_visible(prec) else None,
                    "fields": p_fields,
                    # positional cells aligned to `associate_attrs` for the wide
                    # class-list table (one column per associate attribute).
                    "cells": p_cells,
                }
            )
        rows.append(
            {
                "record": child,
                "subject_display": subject_display,
                "fields": fields,
                "member_grid": member_grid,
                "parents": parents,
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
