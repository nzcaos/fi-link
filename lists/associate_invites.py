"""Invitations for proxy-captured parents (CLAUDE.md / *Family-association
model*, "Adding a parent later").

A second parent entered by the first one in the onboarding wizard (or later
via *Weitere Bezugsperson hinzufügen*) is a PERSON without a USER. List mail
goes only to the Benutzergruppe (`ListAccess`, USER-based), so such a parent
silently received nothing — the 2026-10 class-mail incident. Rather than
mailing a person who never consented, we invite them: an inactive stub USER on
their *existing* PERSON plus a personal `LIST_INVITE_TOKEN` (target_person =
that PERSON), carried by an activation link. Activating takes the proxy record
over (`lists.views._take_over_associate_record`): ListAccess, own record
manager, guardian of the child — no second PERSON, no wizard.

Not invited: a PERSON without email, one that already has a USER, and one whose
address already belongs to an account (shared family mailbox — that mailbox
already receives the list's mail, and a stub on it would usually be a
duplicate of an existing account). `manage.py invite_proxy_associates` is the
backfill for parents captured before this existed.
"""
from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import ActivationToken, Person, User

from .models import List, ListInviteToken, ListRecord, PersonRelationship

log = logging.getLogger(__name__)

INVITE_TTL = timedelta(days=30)


@dataclass
class AssociateInvitation:
    activation: ActivationToken
    list: List
    invited_by: User | None


def skip_reason(person: Person) -> str | None:
    """Why `person` is not invited, or None if they can be."""
    email = (person.email or "").strip()
    if not email:
        return "keine E-Mail-Adresse"
    if User.objects.filter(person=person).exists():
        return "hat bereits ein Konto"
    shared = (
        Person.objects.filter(email__iexact=email, user__isnull=False)
        .exclude(pk=person.pk)
        .first()
    )
    if shared is not None:
        return f"Adresse gehört bereits zum Konto von {shared}"
    return None


def create_invitation(
    *, person: Person, list_obj: List, invited_by: User | None
) -> AssociateInvitation | None:
    """Write the stub USER, invitation and activation link for `person` (no
    mail — see `send_invitation`). Call inside the transaction that created
    the proxy record. Returns None when `skip_reason` applies or the list is
    archived."""
    if list_obj.archived_at is not None or skip_reason(person) is not None:
        return None
    from accounts.webauthn_service import random_username

    # No privacy consent stamped: the PrivacyConsentMiddleware asks for it on
    # the first page after activation, before the invitation is accepted.
    stub = User.objects.create_user(
        person=person, username=random_username(), is_active=False
    )
    invite = ListInviteToken.objects.create(
        list=list_obj,
        invited_by=invited_by,
        target_email=person.email.strip().lower(),
        target_person=person,
        mode=list_obj.template.member_subject_mode,
        expires_at=timezone.now() + INVITE_TTL,
    )
    activation = ActivationToken.issue(
        person=person,
        email=invite.target_email,
        user=stub,
        ttl=INVITE_TTL,
        pending_key="pending_invite_token",
        pending_token=invite.token,
        pending_list=list_obj,
    )
    return AssociateInvitation(activation=activation, list=list_obj, invited_by=invited_by)


def _children_phrase(person: Person, list_obj: List) -> str:
    """'Vater von Lina Müller' (or several, joined) for the mail body."""
    child_ids = ListRecord.objects.filter(
        list=list_obj, role=ListRecord.Role.MEMBER, archived_at__isnull=True
    ).values_list("subject_id", flat=True)
    rels = (
        PersonRelationship.objects.filter(related_person=person, subject_person_id__in=child_ids)
        .select_related("subject_person")
        .order_by("pk")
    )
    return ", ".join(f"{r.role} {r.subject_person}".strip() for r in rels)


def _record_creator(person: Person, list_obj: List) -> User | None:
    """The parent who captured `person` as proxy (RecordManager basis=creator
    on their associate record) — named in the mail and used as inviter."""
    from .models import RecordManager

    rm = (
        RecordManager.objects.filter(
            record__list=list_obj,
            record__subject=person,
            record__role=ListRecord.Role.ASSOCIATE,
            basis=RecordManager.Basis.CREATOR,
        )
        .select_related("user__person")
        .order_by("pk")
        .first()
    )
    return rm.user if rm is not None else None


def invitation_body(person: Person, list_obj: List, invited_by, link: str, expires_at) -> str:
    if invited_by is None:
        invited_by = _record_creator(person, list_obj)
    who = str(invited_by.person) if invited_by is not None else "Jemand"
    as_what = _children_phrase(person, list_obj)
    entered = f"als {as_what} " if as_what else ""
    return (
        f"Hallo {person.given_name},\n\n"
        f"{who} hat Sie {entered}in die Liste „{list_obj.title}“ bei Fichtelink "
        f"eingetragen.\n\n"
        f"Damit Sie die E-Mails an diese Liste bekommen und selbst festlegen können, "
        f"welche Ihrer Angaben die anderen Eltern sehen, aktivieren Sie bitte Ihren "
        f"Zugang:\n\n{link}\n\n"
        f"Der Link ist bis {timezone.localtime(expires_at):%d.%m.%Y} gültig. Falls Ihr "
        f"Gerät keinen Passkey einrichten kann, klicken Sie auf der Seite auf "
        f"„Passkey klappt nicht?“ — Fichtelink funktioniert auch ohne Passkey, die "
        f"Anmeldung läuft dann über einen Link per E-Mail.\n\n"
        f"Solange Sie den Link nicht nutzen, bekommen Sie keine E-Mails über die "
        f"Liste. Wenn Sie das nicht möchten, ignorieren Sie diese Nachricht einfach."
    )


def send_invitation(inv: AssociateInvitation) -> bool:
    """Mail the activation link. Call after the creating transaction has
    committed. False on a mail-server error (logged; the stub then shows up
    under *Nicht abgeschlossene Anmeldungen* for a re-send)."""
    token = inv.activation
    person = token.person
    link = f"{(settings.RP_ORIGIN or '').rstrip('/')}{reverse('accounts:activate', args=[token.token])}"
    try:
        send_mail(
            subject=f"Fichtelink — Einladung zur Liste „{inv.list.title}“",
            message=invitation_body(person, inv.list, inv.invited_by, link, token.expires_at),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[token.email],
        )
    except (smtplib.SMTPException, OSError) as exc:
        log.error("associate invitation for person=%s failed: %s", person.pk, exc)
        return False
    log.info("associate invitation sent: person=%s list=%s", person.pk, inv.list.pk)
    return True


def proxy_associates_without_account(list_obj: List | None = None):
    """Active associate records whose PERSON has no USER — the backfill's
    candidates (each with its `skip_reason` evaluated by the caller)."""
    qs = ListRecord.objects.filter(
        role=ListRecord.Role.ASSOCIATE,
        archived_at__isnull=True,
        list__archived_at__isnull=True,
        subject__user__isnull=True,
    ).select_related("subject", "list__template")
    if list_obj is not None:
        qs = qs.filter(list=list_obj)
    return qs.order_by("list__title", "subject__family_name", "subject__given_name")
