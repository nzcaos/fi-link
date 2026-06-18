"""School-class lifecycle logic (Phase 6): cohort rollover.

Pure-ish, testable functions separated from the HTTP layer (same split as
``inbound_pipeline.py``). The views call ``plan_rollover`` to render the
preview and ``execute_rollover`` to apply the super-admin's reviewed plan.

Cohort encoding (see CLAUDE.md / *School-class lifecycle* and the
``List.cohort_grade`` help text). ``cohort_track`` is the parallel-class letter
for lettered classes and **null for Kursstufe** — that is the authoritative
"is this a Kursstufe list?" signal. K1/K2 are then distinguished by
``cohort_grade`` as the stage just above the last lettered grade L:

    curriculum   last lettered grade L   K1        K2
    G8           10                      11        12
    G9           11                      12        13

(The help text's "5–12" is guidance; the field is not range-constrained, so a
G9 K2 at grade 13 is allowed.) Only lists whose ``curriculum_track`` is set
participate — other templates (Förderverein, VHS) leave it null and are skipped.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from .models import List, ListAccess, ListAdmin, ListRecord

# Last grade that still carries a parallel-class letter, per curriculum. Above
# this the class unit dissolves into Kursstufe (K1 = L+1, K2 = L+2).
LAST_LETTERED_GRADE = {"G8": 10, "G9": 11}


# ---------------------------------------------------------------------------
# Classification + default labels
# ---------------------------------------------------------------------------


def _classify(lst: List) -> tuple[str, int | None, str | None]:
    """Return ``(kind, new_grade, new_track)`` for one list.

    kind ∈ {advance, merge, k1_to_k2, k2_archive, none}. ``none`` means the
    list does not participate (or is in an unexpected state) and is skipped.
    """
    cur = lst.curriculum_track
    L = LAST_LETTERED_GRADE.get(cur)
    g = lst.cohort_grade
    track = (lst.cohort_track or "").strip() or None
    if L is None or g is None:
        return ("none", None, None)
    if track is not None:
        if g < L:
            return ("advance", g + 1, track)
        if g == L:
            return ("merge", L + 1, None)
        return ("none", None, None)
    # Kursstufe (track is null).
    if g == L + 1:
        return ("k1_to_k2", L + 2, None)
    if g == L + 2:
        return ("k2_archive", None, None)
    return ("none", None, None)


def default_label(grade: int, track: str | None, curriculum: str) -> tuple[str, str]:
    """Suggested ``(title, email_alias)`` for a target cohort. The rollover
    wizard pre-fills these; the super-admin may edit before executing. Only
    called for ``advance`` / ``k1_to_k2`` / ``merge`` targets (grade is set).
    """
    if track:
        return (f"Klasse {grade}{track}", f"{grade}{track}")
    L = LAST_LETTERED_GRADE.get(curriculum)
    stage = grade - L if L is not None else grade  # 1 → K1, 2 → K2
    return (f"Kursstufe K{stage}", f"k{stage}")


def _current_label(lst: List) -> str:
    if lst.cohort_track:
        return f"{lst.cohort_grade}{lst.cohort_track}"
    L = LAST_LETTERED_GRADE.get(lst.curriculum_track)
    if L and lst.cohort_grade:
        return f"K{lst.cohort_grade - L}"
    return lst.title


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class ListAction:
    """A per-list transform: advance, k1_to_k2, or k2_archive."""

    list_id: int
    title: str
    current_label: str
    kind: str
    new_grade: int | None
    new_track: str | None
    default_title: str
    default_alias: str


@dataclass
class MergeGroup:
    """The N:1 merge of all grade-L lettered lists (within one curriculum +
    parent subtree) into a single fresh K1 list."""

    key: str
    curriculum: str
    parent_id: int | None
    source_list_ids: list[int]
    source_labels: list[str]
    new_grade: int
    default_title: str
    default_alias: str


@dataclass
class RolloverPlan:
    actions: list[ListAction] = field(default_factory=list)
    merges: list[MergeGroup] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.actions and not self.merges


def _dedup_alias(base: str, taken: set[str]) -> str:
    """Return ``base`` if free, else ``base-2`` / ``base-3`` / … — the first
    that isn't in ``taken`` — and register the result in ``taken``. The suffix
    keeps the alias valid (``k1-2`` still starts with a letter / ends with a
    digit, per lists.forms._ALIAS_RE).
    """
    if base not in taken:
        taken.add(base)
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    alias = f"{base}-{n}"
    taken.add(alias)
    return alias


def plan_rollover(lists=None) -> RolloverPlan:
    """Compute the proposed rollover for the given lists (default: every
    non-archived list with a curriculum_track set). Side-effect free.
    """
    if lists is None:
        lists = (
            List.objects.filter(archived_at__isnull=True)
            .exclude(curriculum_track__isnull=True)
            .exclude(curriculum_track="")
            .order_by("curriculum_track", "cohort_grade", "cohort_track")
        )
    actions: list[ListAction] = []
    merge_buckets: dict[str, list[List]] = {}

    for lst in lists:
        kind, new_grade, new_track = _classify(lst)
        if kind == "none":
            continue
        if kind == "merge":
            # One K1 per (curriculum, parent subtree) so independent subtrees
            # don't get fused into a single Kursstufe.
            key = f"{lst.curriculum_track}:{lst.parent_id or 0}"
            merge_buckets.setdefault(key, []).append(lst)
            continue
        if kind == "k2_archive":
            # No target cohort — the list is just archived. Defaults are
            # display-only here (the wizard renders no inputs for this row).
            dt, da = lst.title, lst.email_alias
        else:
            dt, da = default_label(new_grade, new_track, lst.curriculum_track)
        actions.append(
            ListAction(
                list_id=lst.id,
                title=lst.title,
                current_label=_current_label(lst),
                kind=kind,
                new_grade=new_grade,
                new_track=new_track,
                default_title=dt,
                default_alias=da,
            )
        )

    # Merge default aliases must not collide. Two merge buckets legitimately
    # produce the same default: a G8 merge (grade 10 → K1) and a G9 merge
    # (grade 11 → K1) both default to "k1", as do two independent subtrees of
    # the same curriculum. execute_rollover creates each K1 with List.objects.
    # create(email_alias=…) under the global-unique constraint, so colliding
    # defaults would raise IntegrityError and atomically roll the whole rollover
    # back. Disambiguate up front so the preview already shows distinct, valid
    # aliases (the super-admin may still override). Seed the taken-set with every
    # non-archived alias the rollover does *not* itself free, plus the advancing
    # / k1→k2 target aliases, so a merge default can't collide with those either.
    freed_ids = {a.list_id for a in actions}
    for srcs in merge_buckets.values():
        freed_ids.update(s.id for s in srcs)
    taken_aliases = set(
        List.objects.filter(archived_at__isnull=True)
        .exclude(id__in=freed_ids)
        .values_list("email_alias", flat=True)
    )
    taken_aliases.update(
        a.default_alias for a in actions if a.kind in ("advance", "k1_to_k2")
    )
    # Parent titles for the disambiguating hint appended to a suffixed title.
    parent_ids = {srcs[0].parent_id for srcs in merge_buckets.values() if srcs[0].parent_id}
    parent_titles = dict(
        List.objects.filter(id__in=parent_ids).values_list("id", "title")
    )

    merges: list[MergeGroup] = []
    for key, srcs in merge_buckets.items():
        cur = srcs[0].curriculum_track
        new_grade = LAST_LETTERED_GRADE[cur] + 1
        dt, da = default_label(new_grade, None, cur)
        unique_da = _dedup_alias(da, taken_aliases)
        if unique_da != da:
            # Make the suffixed alias legible in the preview: name the subtree
            # (parent) it came from, falling back to the curriculum.
            hint = parent_titles.get(srcs[0].parent_id) or cur
            dt = f"{dt} ({hint})"
        merges.append(
            MergeGroup(
                key=key,
                curriculum=cur,
                parent_id=srcs[0].parent_id,
                source_list_ids=[s.id for s in srcs],
                source_labels=[_current_label(s) for s in srcs],
                new_grade=new_grade,
                default_title=dt,
                default_alias=unique_da,
            )
        )
    return RolloverPlan(actions=actions, merges=merges)


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def _retire_and_archive(lst: List, now) -> None:
    """Archive a list and free its human-friendly alias so an advancing list
    can claim it. Mail to the retired alias no longer resolves (resolve_alias
    only matches non-archived lists), which is the intended "old alias bounces"
    behaviour (CLAUDE.md / *No grace period*).
    """
    lst.email_alias = f"{lst.email_alias}__arch_{lst.id}"[:100]
    lst.archived_at = now
    lst.save(update_fields=["email_alias", "archived_at"])


def _merge_sources_into(sources: list[List], k1: List, now) -> None:
    """Re-parent active member/associate records from the source lists onto the
    new K1, carry source admins + managers' Benutzergruppe access along, then
    archive the sources (alias retired).
    """
    manager_user_ids: set[int] = set()
    admin_user_ids: set[int] = set()
    for src in sources:
        for rec in src.records.filter(archived_at__isnull=True):
            already = ListRecord.objects.filter(
                list=k1, subject=rec.subject, archived_at__isnull=True
            ).exists()
            if already:
                # e.g. a parent (associate) with children in two source
                # classes — keep the one already moved, archive the duplicate.
                rec.archived_at = now
                rec.save(update_fields=["archived_at"])
                continue
            rec.list = k1
            rec.save(update_fields=["list"])
            manager_user_ids.update(rec.managers.values_list("user_id", flat=True))
        admin_user_ids.update(src.admins.values_list("user_id", flat=True))

    for uid in admin_user_ids:
        ListAdmin.objects.get_or_create(list=k1, user_id=uid)
    for uid in manager_user_ids:
        ListAccess.objects.get_or_create(list=k1, user_id=uid)

    for src in sources:
        _retire_and_archive(src, now)


@transaction.atomic
def execute_rollover(plan: RolloverPlan, overrides: dict | None = None) -> dict:
    """Apply a reviewed plan atomically.

    ``overrides`` lets the wizard pass edited titles/aliases::

        {"list": {list_id: {"title": ..., "alias": ...}},
         "merge": {merge_key: {"title": ..., "alias": ...}}}

    Order matters for the globally-unique ``email_alias`` constraint: archive
    (and alias-retire) merge sources + K2 first, then create K1 lists, then
    mutate the advancing/k1→k2 lists via a two-phase temp-rename so no two
    rows transiently hold the same alias.
    """
    overrides = overrides or {}
    list_ov = overrides.get("list", {})
    merge_ov = overrides.get("merge", {})
    now = timezone.now()
    results = {"advanced": [], "k1_to_k2": [], "merged_into": [], "archived": []}

    ids = [a.list_id for a in plan.actions]
    for m in plan.merges:
        ids.extend(m.source_list_ids)
    locked = {
        l.id: l for l in List.objects.select_for_update().filter(id__in=ids)
    }
    mutating = [a for a in plan.actions if a.kind in ("advance", "k1_to_k2")]

    # 1) Park every mutating list on a temporary unique alias FIRST. This frees
    #    all their human aliases up-front so neither the new merge K1 (wants
    #    "k1" while last year's K1 — now advancing to K2 — still holds it) nor a
    #    downward advance (9a→10a, 8a→9a, …) can transiently collide.
    for a in mutating:
        lst = locked[a.list_id]
        lst.email_alias = f"__roll_tmp_{lst.id}"
        lst.save(update_fields=["email_alias"])

    # 2) Archive K2 lists and merge sources (retire their aliases too).
    for a in plan.actions:
        if a.kind == "k2_archive":
            _retire_and_archive(locked[a.list_id], now)
            results["archived"].append(a.list_id)

    # 3) Merges: create the new K1 list, re-parent records, archive sources.
    for m in plan.merges:
        ov = merge_ov.get(m.key, {})
        title = (ov.get("title") or m.default_title).strip()
        alias = (ov.get("alias") or m.default_alias).strip().lower()
        sources = [locked[sid] for sid in m.source_list_ids]
        first = sources[0]
        k1 = List.objects.create(
            title=title,
            email_alias=alias,
            template=first.template,
            parent=first.parent,
            visibility=first.visibility,
            cohort_grade=m.new_grade,
            cohort_track=None,
            curriculum_track=m.curriculum,
        )
        _merge_sources_into(sources, k1, now)
        results["merged_into"].append(k1.id)
        results["archived"].extend(m.source_list_ids)

    # 4) Assign final aliases/titles/cohort to the parked mutating lists. Every
    #    target alias is now free (old holders parked in step 1 or retired in
    #    steps 2–3).
    for a in mutating:
        lst = locked[a.list_id]
        ov = list_ov.get(a.list_id) or list_ov.get(str(a.list_id)) or {}
        lst.title = (ov.get("title") or a.default_title).strip()
        lst.email_alias = (ov.get("alias") or a.default_alias).strip().lower()
        lst.cohort_grade = a.new_grade
        lst.cohort_track = a.new_track
        lst.save(
            update_fields=["title", "email_alias", "cohort_grade", "cohort_track", "updated_at"]
        )
        results["advanced" if a.kind == "advance" else "k1_to_k2"].append(a.list_id)

    return results
