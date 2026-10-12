"""Erasure of a PERSON and everything that hangs off it (DSGVO Art. 17).

`plan_erasure` works out what would be deleted; `execute_erasure` deletes it
in one transaction. Driven by `manage.py delete_person` — there is no
self-service delete in the UI yet; the privacy overview for parents promises
deletion "on request", and this is how the operator fulfils it.

Why this is not just `Person.delete()`: `User.person`, `ListRecord.subject`
and several "created/granted by" user FKs are PROTECT, so a Person with any
record — even an archived one — or a User who ever invited someone cannot be
deleted through the ORM or the Django admin. This module removes the records
first and hands the PROTECT audit references over to a replacement user
(normally a super-admin) instead of losing the rows they protect.

Scope:
- the target PERSON, their USER (passkeys, memberships, admin rights, record
  managers, form signups; the Matrix account is redacted + deactivated on
  Synapse first) and all their LIST_RECORDs including archived ones (values, visibility rows cascade);
- children (PERSONs without USER) whose only guardians are being erased —
  otherwise they would be left as records nobody can manage — plus co-parents
  without USER that were captured by proxy only for those children;
- mail metadata, invitation and activation tokens addressed to the erased
  persons' email addresses, unless another remaining PERSON shares the address
  (shared family mailbox), in which case those rows stay and age out normally.

Not touched: finished task-queue jobs (deleted after TASK_JOB_RETENTION_DAYS),
container logs (size-rotated), backups (KEEP_DAYS in scripts/backup.sh).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from accounts.models import ActivationToken, Person, User
from forms.models import Form

from .models import (
    AdminInviteToken,
    AggregateAlias,
    InboundMessage,
    List,
    ListAdmin,
    ListInviteToken,
    ListJoinToken,
    ListRecord,
    ListSendPermission,
    OutboundMessage,
    PendingTransfer,
    PersonRelationship,
)

# (model, user FK field) pairs whose on_delete=PROTECT blocks deleting a User.
_PROTECTED_USER_REFS = (
    (ListSendPermission, "granted_by"),
    (AggregateAlias, "created_by"),
    (ListInviteToken, "invited_by"),
    (ListJoinToken, "created_by"),
    (PendingTransfer, "requested_by"),
    (Form, "created_by"),
)


@dataclass
class ErasurePlan:
    persons: list[Person]
    users: list[User]
    record_count: int
    emails: list[str]
    """Addresses no remaining PERSON uses — mail rows/tokens for these go."""
    protected_refs: dict[str, int] = field(default_factory=dict)
    """`Model.field` → number of rows that need a replacement user."""
    sole_admin_lists: list[List] = field(default_factory=list)


def _has_user(person: Person) -> bool:
    return User.objects.filter(person=person).exists()


def plan_erasure(person: Person, *, include_children: bool = True) -> ErasurePlan:
    persons = [person]
    ids = {person.pk}

    if include_children:
        children = []
        for rel in PersonRelationship.objects.filter(related_person=person).select_related(
            "subject_person"
        ):
            child = rel.subject_person
            if child.pk in ids or _has_user(child):
                continue
            other_guardian = (
                PersonRelationship.objects.filter(
                    subject_person=child, related_person__user__isnull=False
                )
                .exclude(related_person_id__in=ids)
                .exists()
            )
            if not other_guardian:
                children.append(child)
                persons.append(child)
                ids.add(child.pk)
        # Co-parents captured by proxy (no USER) whose every relationship
        # points at a child that is going away.
        for child in children:
            for rel in PersonRelationship.objects.filter(subject_person=child).select_related(
                "related_person"
            ):
                co = rel.related_person
                if co.pk in ids or _has_user(co):
                    continue
                if (
                    PersonRelationship.objects.filter(related_person=co)
                    .exclude(subject_person_id__in=ids)
                    .exists()
                ):
                    continue
                persons.append(co)
                ids.add(co.pk)

    users = list(User.objects.filter(person_id__in=ids))
    user_ids = [u.pk for u in users]

    candidate_emails = {p.email.strip().lower() for p in persons if p.email}
    emails = sorted(
        e
        for e in candidate_emails
        if not Person.objects.filter(email__iexact=e).exclude(pk__in=ids).exists()
    )

    protected_refs = {}
    for model, fk in _PROTECTED_USER_REFS:
        n = model.objects.filter(**{f"{fk}_id__in": user_ids}).count()
        if n:
            protected_refs[f"{model.__name__}.{fk}"] = n

    sole_admin_lists = [
        la.list
        for la in ListAdmin.objects.filter(user_id__in=user_ids).select_related("list")
        if not ListAdmin.objects.filter(list=la.list).exclude(user_id__in=user_ids).exists()
    ]

    return ErasurePlan(
        persons=persons,
        users=users,
        record_count=ListRecord.objects.filter(subject_id__in=ids).count(),
        emails=emails,
        protected_refs=protected_refs,
        sole_admin_lists=sole_admin_lists,
    )


def _email_q(field_name: str, emails: list[str]) -> Q:
    q = Q(pk__in=[])
    for e in emails:
        q |= Q(**{f"{field_name}__iexact": e})
    return q


def _erase_matrix_accounts(users: list[User]) -> None:
    """Redact + deactivate the users' Matrix accounts on Synapse before the
    MatrixAccount rows cascade away (afterwards nobody knows the Matrix ids).
    Runs before the DB transaction and raises on failure, so an unreachable
    Synapse aborts the erasure instead of leaving a live account behind.
    With Matrix switched off Synapse is not contacted; the rows just cascade."""
    import logging

    from matrix.models import MatrixAccount

    accounts = list(MatrixAccount.objects.filter(user__in=users))
    if not accounts:
        return
    if not settings.MATRIX_ENABLED:
        logging.getLogger(__name__).warning(
            "erasure: MATRIX_ENABLED is off — %d Matrix account(s) not erased on Synapse: %s",
            len(accounts),
            ", ".join(a.matrix_user_id for a in accounts),
        )
        return
    from matrix import service

    for account in accounts:
        service.erase_matrix_account(account)


def execute_erasure(plan: ErasurePlan, *, reassign_to: User | None) -> dict[str, int]:
    if plan.protected_refs and reassign_to is None:
        raise ValueError("protected references need a replacement user")
    user_ids = [u.pk for u in plan.users]
    if reassign_to is not None and reassign_to.pk in user_ids:
        raise ValueError("replacement user is being erased")
    person_ids = [p.pk for p in plan.persons]

    _erase_matrix_accounts(plan.users)

    counts: dict[str, int] = {}
    with transaction.atomic():
        for model, fk in _PROTECTED_USER_REFS:
            n = model.objects.filter(**{f"{fk}_id__in": user_ids}).update(**{fk: reassign_to})
            if n:
                counts[f"reassigned {model.__name__}.{fk}"] = n

        deletes = [
            ListRecord.objects.filter(subject_id__in=person_ids),
            User.objects.filter(pk__in=user_ids),
            Person.objects.filter(pk__in=person_ids),
        ]
        if plan.emails:
            deletes += [
                InboundMessage.objects.filter(_email_q("from_email", plan.emails)),
                OutboundMessage.objects.filter(
                    _email_q("recipient_email", plan.emails)
                    | _email_q("from_email", plan.emails)
                ),
                ListInviteToken.objects.filter(_email_q("target_email", plan.emails)),
                AdminInviteToken.objects.filter(_email_q("to_email", plan.emails)),
                ActivationToken.objects.filter(_email_q("email", plan.emails)),
            ]
        # Order matters: records before persons (subject PROTECT), users
        # before persons (User.person PROTECT).
        for qs in deletes:
            _, per_model = qs.delete()
            for label, n in per_model.items():
                if n:
                    counts[label] = counts.get(label, 0) + n
    return counts
