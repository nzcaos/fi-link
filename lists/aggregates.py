"""Aggregate-alias resolution and the aggregate send decision.

An ``AggregateAlias`` (e.g. ``eltern@<domain>``) has no membership of its own —
it fans out across many lists by relationship role. Everything here is pure and
unit-testable: subtree traversal, recipient resolution from PERSON_RELATIONSHIP,
target-list resolution, and the permission decision. The orchestration that
writes rows / defers tasks lives in ``lists.tasks``.

CLAUDE.md / "Aggregate email aliases":
- Recipient resolution is at send time, not cached.
- Send permission is derived from the LIST_SEND_PERMISSION graph (no separate
  permission table): the sender must hold send permission on **every** target
  list the alias resolves to; ``requires_release_click`` is the strictest of
  the per-list values; otherwise → super-admin approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .inbound_pipeline import resolve_send_permission
from .models import AggregateAlias, List, ListAccess, ListRecord


def subtree_list_ids(scope_list_id: int | None) -> set[int]:
    """Ids of all non-archived lists in the subtree rooted at ``scope_list_id``
    (the root included). ``None`` scope means the whole installation.

    Tree walk (List hierarchy is a tree in v1 — no N:M parents), guarded by the
    visited set so a malformed parent edge can't loop forever.
    """
    base = List.objects.filter(archived_at__isnull=True)
    if scope_list_id is None:
        return set(base.values_list("pk", flat=True))

    ids: set[int] = set()
    frontier: set[int] = {scope_list_id}
    while frontier:
        ids |= frontier
        frontier = set(
            base.filter(parent_id__in=frontier)
            .exclude(pk__in=ids)
            .values_list("pk", flat=True)
        )
    return ids


def aggregate_recipient_emails(agg: AggregateAlias) -> list[str]:
    """Deduped, deliverable recipient addresses for ``agg``.

    A Person is a recipient iff they are the ``related_person`` of a
    PERSON_RELATIONSHIP whose role is in ``included_roles`` and whose
    ``subject_person`` (the member, e.g. the child) holds an active *member*
    record in a list within the scope subtree. Restricted to non-empty
    ``Person.email``; deduped per address (a shared family mailbox counts once).
    """
    from accounts.models import Person  # local — avoid import cycle

    subtree = subtree_list_ids(agg.scope_list_id)
    if not subtree or not agg.included_roles:
        return []
    emails = (
        Person.objects.filter(
            relationships_as_related__role__in=agg.included_roles,
            relationships_as_related__subject_person__records__list_id__in=subtree,
            relationships_as_related__subject_person__records__role=ListRecord.Role.MEMBER,
            relationships_as_related__subject_person__records__archived_at__isnull=True,
        )
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .values_list("email", flat=True)
        .distinct()
    )
    return list(emails)


def aggregate_target_list_ids(agg: AggregateAlias) -> set[int]:
    """Ids of the lists in the scope subtree that actually contribute
    recipients — i.e. lists holding an active *member* record whose subject
    carries a relationship with a role in ``included_roles``.

    This is the set the permission check runs over: the sender must be permitted
    on every one of these.
    """
    subtree = subtree_list_ids(agg.scope_list_id)
    if not subtree or not agg.included_roles:
        return set()
    return set(
        ListRecord.objects.filter(
            list_id__in=subtree,
            role=ListRecord.Role.MEMBER,
            archived_at__isnull=True,
            subject__relationships_as_subject__role__in=agg.included_roles,
        )
        .values_list("list_id", flat=True)
        .distinct()
    )


class AggregateOutcome(str, Enum):
    """What to do with an aggregate-addressed message once suppression passed."""

    FORWARD = "forward"  # permitted on every target, no click → fan out now
    PERMITTED_RELEASE = "permitted_release"  # permitted, but a release click is required
    SUPER_ADMIN_APPROVAL = "super_admin_approval"  # not permitted → super-admin decides


@dataclass(frozen=True)
class AggregateDecision:
    outcome: AggregateOutcome
    target_list_ids: frozenset[int]


def decide_aggregate(agg: AggregateAlias, sender_users) -> AggregateDecision:
    """Resolve the send decision for ``agg`` (CLAUDE.md / "Aggregate email
    aliases"):

    1. Resolve the target-list set.
    2. The sender is permitted iff they hold send permission (direct / implicit
       parent / transitive) on **every** target list.
    3. Permitted + no list needs a click → FORWARD. Permitted + any list needs a
       click → PERMITTED_RELEASE (release link to the sender).
    4. No sender USER, an empty target set (nothing carries the roles — likely a
       misconfiguration), or permission missing on any list → SUPER_ADMIN_APPROVAL.
    """
    sender_users = list(sender_users)
    target_ids = aggregate_target_list_ids(agg)
    frozen = frozenset(target_ids)
    if not sender_users or not target_ids:
        return AggregateDecision(AggregateOutcome.SUPER_ADMIN_APPROVAL, frozen)

    member_list_ids = set(
        ListAccess.objects.filter(user__in=sender_users).values_list(
            "list_id", flat=True
        )
    )

    requires_click = False
    for lst in List.objects.filter(pk__in=target_ids):
        permitted, click = resolve_send_permission(lst, member_list_ids)
        if not permitted:
            return AggregateDecision(AggregateOutcome.SUPER_ADMIN_APPROVAL, frozen)
        requires_click = requires_click or click

    outcome = (
        AggregateOutcome.PERMITTED_RELEASE if requires_click else AggregateOutcome.FORWARD
    )
    return AggregateDecision(outcome, frozen)
