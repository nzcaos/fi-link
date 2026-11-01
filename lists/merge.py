"""Merge a duplicate PERSON into another (`manage.py merge_person`).

Typical duplicate: parent B was captured by parent A as second parent (a
PERSON without USER, carrying the visible associate record), and B then
registered on their own — a second PERSON with the USER. B gets the list mail
but cannot manage the record everyone sees. Children get duplicated the same
way when both parents ran the wizard for the same child.

`plan_merge(source, target)` works out what happens, `execute_merge` does it
in one transaction. The source must not have a USER: accounts are never
merged (passkeys, consent, sessions belong to one login), so the account side
is always the target.

What moves from source to target:
- LIST_RECORDs: re-pointed to the target. Where the target already has an
  active record in the same list, the target's record is kept (it is the one
  the person maintains), attribute values it lacks are copied over from the
  source record, the source record's managers are added to it, and the source
  record is deleted (values and visibility rows cascade).
- If the target has a USER: they manage every record they now own
  (self_registered), are guardian of every child they are now related to, and
  join the Benutzergruppe of each active list they have a record in — that is
  what makes the list mail reach them.
- PERSON_RELATIONSHIPs in both directions (dropped where the target is
  already related to that person, whatever the role), invitations naming the source, pending transfers.
- The source's email, if the target has none. Then the source is deleted.

`suggest_merges()` lists candidate pairs for the operator to review; it never
merges by itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Q

from accounts.models import ActivationToken, Person, User

from .models import (
    ListAccess,
    ListInviteToken,
    ListRecord,
    ListRecordValue,
    PendingTransfer,
    PersonRelationship,
    RecordManager,
)


class MergeError(Exception):
    pass


@dataclass
class MergePlan:
    source: Person
    target: Person
    target_user: User | None
    moved_records: list = field(default_factory=list)  # source records re-pointed
    collapsed: list = field(default_factory=list)  # (source_record, kept_target_record)
    relationships: list = field(default_factory=list)  # PersonRelationship rows touching source
    lines: list = field(default_factory=list)  # human-readable plan


def _user_of(person: Person) -> User | None:
    return User.objects.filter(person=person).first()


def plan_merge(source: Person, target: Person) -> MergePlan:
    if source.pk == target.pk:
        raise MergeError("Quelle und Ziel sind dieselbe Person.")
    if _user_of(source) is not None:
        raise MergeError(
            f"{source} (person={source.pk}) hat ein Konto. Konten werden nicht "
            f"zusammengeführt — die Person mit Konto muss das Ziel sein (--into)."
        )
    plan = MergePlan(source=source, target=target, target_user=_user_of(target))
    plan.lines.append(
        f"Quelle: person={source.pk} {source} <{source.email or '-'}> (ohne Konto)"
    )
    plan.lines.append(
        f"Ziel:   person={target.pk} {target} <{target.email or '-'}> "
        + (f"(Konto user={plan.target_user.pk})" if plan.target_user else "(ohne Konto)")
    )

    target_active = {
        r.list_id: r
        for r in ListRecord.objects.filter(subject=target, archived_at__isnull=True)
    }
    for rec in ListRecord.objects.filter(subject=source).select_related("list").order_by("pk"):
        kept = target_active.get(rec.list_id) if rec.archived_at is None else None
        if kept is None:
            plan.moved_records.append(rec)
            plan.lines.append(f"  Eintrag {rec.pk} ({rec.role}) in „{rec.list.title}“ → geht an das Ziel")
            continue
        if kept.role != rec.role:
            raise MergeError(
                f"In „{rec.list.title}“ ist die Quelle {rec.role}, das Ziel {kept.role} — "
                f"das passt nicht zusammen, bitte von Hand klären."
            )
        plan.collapsed.append((rec, kept))
        plan.lines.append(
            f"  Eintrag {rec.pk} in „{rec.list.title}“ → entfällt, Eintrag {kept.pk} des "
            f"Ziels bleibt (fehlende Angaben werden übernommen)"
        )

    rels = list(
        PersonRelationship.objects.filter(Q(subject_person=source) | Q(related_person=source))
        .select_related("subject_person", "related_person")
        .order_by("pk")
    )
    for rel in rels:
        other = rel.subject_person if rel.related_person_id == source.pk else rel.related_person
        if other.pk == target.pk:
            raise MergeError(
                f"Quelle und Ziel stehen selbst in Beziehung ({rel}) — das sind keine Duplikate."
            )
        plan.relationships.append(rel)
        if rel.related_person_id == source.pk:
            plan.lines.append(f"  Beziehung: {target} {rel.role} {rel.subject_person}")
        else:
            plan.lines.append(f"  Beziehung: {rel.related_person} {rel.role} {target}")

    if plan.target_user is not None:
        lists = {
            r.list
            for r in plan.moved_records + [kept for _, kept in plan.collapsed]
            if r.archived_at is None and r.list.archived_at is None
        }
        for lst in sorted(lists, key=lambda x: x.title):
            if not ListAccess.objects.filter(list=lst, user=plan.target_user).exists():
                plan.lines.append(f"  Konto tritt „{lst.title}“ bei (bekommt dann die Listen-Mails)")
    for child in _duplicate_children_after(plan):
        plan.lines.append(f"  Hinweis: {child}")
    return plan


def _duplicate_children_after(plan: MergePlan) -> list[str]:
    """Children of the merged person that look like the same child twice in one
    list — typical when both parents ran the wizard. Reported, not merged."""
    child_ids = set(
        PersonRelationship.objects.filter(
            related_person__in=[plan.source, plan.target]
        ).values_list("subject_person_id", flat=True)
    )
    seen: dict = {}
    hints = []
    for rec in (
        ListRecord.objects.filter(
            subject_id__in=child_ids, role=ListRecord.Role.MEMBER, archived_at__isnull=True
        )
        .select_related("subject", "list")
        .order_by("subject_id")
    ):
        key = (rec.list_id, rec.subject.given_name.strip().lower(), rec.subject.family_name.strip().lower())
        if key in seen and seen[key].subject_id != rec.subject_id:
            first = seen[key]
            hints.append(
                f"Kind „{rec.subject}“ steht in „{rec.list.title}“ doppelt (person={first.subject_id} "
                f"und person={rec.subject_id}) — danach ggf. merge_person --from "
                f"{rec.subject_id} --into {first.subject_id}"
            )
        else:
            seen.setdefault(key, rec)
    return hints


@transaction.atomic
def execute_merge(plan: MergePlan) -> None:
    source, target, user = plan.source, plan.target, plan.target_user

    for rec, kept in plan.collapsed:
        have = set(ListRecordValue.objects.filter(record=kept).values_list("attribute_id", flat=True))
        for val in ListRecordValue.objects.filter(record=rec):
            if val.attribute_id not in have and val.value not in (None, ""):
                ListRecordValue.objects.create(record=kept, attribute_id=val.attribute_id, value=val.value)
        for rm in RecordManager.objects.filter(record=rec):
            RecordManager.objects.get_or_create(record=kept, user_id=rm.user_id, defaults={"basis": rm.basis})
        rec.delete()

    for rec in plan.moved_records:
        rec.subject = target
        rec.save(update_fields=["subject"])

    for rel in plan.relationships:
        if rel.related_person_id == source.pk:
            # Any role: the target already being "Mutter von" this child makes
            # the source's "Erziehungsberechtigte von" a duplicate parent cell.
            dup = PersonRelationship.objects.filter(
                subject_person_id=rel.subject_person_id, related_person=target
            )
            if dup.exists():
                rel.delete()
            else:
                rel.related_person = target
                rel.save(update_fields=["related_person"])
        else:
            dup = PersonRelationship.objects.filter(
                subject_person=target, related_person_id=rel.related_person_id
            )
            if dup.exists():
                rel.delete()
            else:
                rel.subject_person = target
                rel.save(update_fields=["subject_person"])

    ListInviteToken.objects.filter(target_person=source).update(target_person=target)
    PendingTransfer.objects.filter(person=source).update(person=target)
    ActivationToken.objects.filter(person=source).delete()

    if user is not None:
        for rec in ListRecord.objects.filter(subject=target).select_related("list"):
            RecordManager.objects.get_or_create(
                record=rec, user=user, defaults={"basis": RecordManager.Basis.SELF_REGISTERED}
            )
            if rec.archived_at is None and rec.list.archived_at is None:
                ListAccess.objects.get_or_create(list=rec.list, user=user)
        child_ids = PersonRelationship.objects.filter(related_person=target).values_list(
            "subject_person_id", flat=True
        )
        for child in ListRecord.objects.filter(
            subject_id__in=child_ids, role=ListRecord.Role.MEMBER, archived_at__isnull=True
        ):
            RecordManager.objects.get_or_create(
                record=child, user=user, defaults={"basis": RecordManager.Basis.GUARDIAN}
            )

    if not (target.email or "").strip() and (source.email or "").strip():
        target.email = source.email
        target.save(update_fields=["email"])
    source.delete()


def _first(name: str) -> str:
    parts = name.strip().lower().split()
    return parts[0] if parts else ""


def _last(name: str) -> str:
    parts = name.strip().lower().replace("-", " ").split()
    return parts[-1] if parts else ""


def suggest_merges() -> list[tuple[Person, Person, str]]:
    """Candidate (source, target, reason) pairs for review. Two PERSONs match
    when they share the first given name and either the email or the last
    family-name word (catches 'Kowolllik', 'Dordor Boskovic'); different first
    names on a shared mailbox (Michael/Rebekka) are not proposed. Only persons
    with an account, a record or a relationship are considered. Each PERSON without USER is
    proposed once: into a matching PERSON with USER (same email first), else
    into the oldest matching PERSON — so triples collapse onto one anchor."""
    people = list(
        Person.objects.filter(
            Q(records__isnull=False)
            | Q(user__isnull=False)
            | Q(relationships_as_related__isnull=False)
            | Q(relationships_as_subject__isnull=False)
        )
        .distinct()
        .select_related("user")
        .order_by("pk")
    )
    has_user = {p.pk: hasattr(p, "user") for p in people}

    def mail(p):
        return (p.email or "").strip().lower()

    pairs = []
    for source in people:
        if has_user[source.pk] or not _first(source.given_name):
            continue
        matches = []
        for other in people:
            if other.pk == source.pk or _first(other.given_name) != _first(source.given_name):
                continue
            same_mail = bool(mail(source)) and mail(source) == mail(other)
            same_family = bool(_last(source.family_name)) and _last(source.family_name) == _last(other.family_name)
            if same_mail or same_family:
                matches.append((other, same_mail))
        with_user = [m for m in matches if has_user[m[0].pk]]
        if with_user:
            target, same_mail = sorted(with_user, key=lambda m: (not m[1], m[0].pk))[0]
        else:
            older = [m for m in matches if m[0].pk < source.pk]
            if not older:
                continue  # source is the anchor of its group
            target, same_mail = min(older, key=lambda m: m[0].pk)
        pairs.append((source, target, "gleiche E-Mail" if same_mail else "gleicher Name, andere E-Mail"))
    return pairs
