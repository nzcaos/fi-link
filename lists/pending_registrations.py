"""Stalled registrations: accounts that registered but never got past the
activation link (CLAUDE.md / "Login by mail link", "Stalled registrations").

A stub (inactive USER, no passkey, never signed in) belongs to the list its
invitation was for — `ActivationToken.pending_list` of its latest activation
link. Class admins see and re-send for their class, super-admins for all;
`manage.py resend_activation` uses the same functions.
"""
from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone

from accounts.grants import grant_is_usable
from accounts.models import ActivationToken, Person, User

from .models import List, ListInviteToken

log = logging.getLogger(__name__)


@dataclass
class PendingRegistration:
    user: User
    person: Person
    list: List | None
    registered_at: datetime
    last_sent_at: datetime | None
    link_valid: bool


def _stubs():
    return (
        User.objects.select_related("person")
        .filter(is_active=False, last_login__isnull=True, passkeys__isnull=True)
        .exclude(person__email__isnull=True)
        .exclude(person__email__exact="")
        .prefetch_related(
            Prefetch(
                "activation_tokens",
                queryset=ActivationToken.objects.filter(
                    purpose=ActivationToken.Purpose.ACTIVATE
                ).select_related("pending_list").order_by("-pk"),
                to_attr="activate_tokens",
            )
        )
        .order_by("person__family_name", "person__given_name", "pk")
    )


def _entry(user: User) -> PendingRegistration:
    latest = user.activate_tokens[0] if user.activate_tokens else None
    return PendingRegistration(
        user=user,
        person=user.person,
        list=latest.pending_list if latest is not None else None,
        registered_at=user.date_joined,
        last_sent_at=latest.created_at if latest is not None else None,
        link_valid=latest is not None and latest.is_valid(),
    )


def pending_registrations(list_obj: List | None = None) -> list[PendingRegistration]:
    """All stalled registrations, or only those whose invitation was for
    `list_obj`."""
    entries = [_entry(u) for u in _stubs()]
    if list_obj is not None:
        entries = [e for e in entries if e.list is not None and e.list.pk == list_obj.pk]
    return entries


def get_pending_registration(user_pk: int) -> PendingRegistration | None:
    user = _stubs().filter(pk=user_pk).first()
    return _entry(user) if user is not None else None


def resend_activation(entry: PendingRegistration, *, by_user: User | None = None) -> bool:
    """Mail a fresh activation link. Returns False if the mail failed (the
    token is created either way, as in registration).

    The link keeps the invitation of the previous one while that is still
    usable. Otherwise, when an admin (`by_user`) sends it for a known list, a
    new personal invitation to that list is attached — the admin re-sending is
    the authorisation, like inviting someone. Without both, the person lands
    without a list and opens the class's QR code again.
    """
    person = entry.person
    previous = entry.user.activate_tokens[0] if entry.user.activate_tokens else None
    grant: dict = {}
    if previous is not None and grant_is_usable(previous.pending_key, previous.pending_token):
        grant = {
            "pending_key": previous.pending_key,
            "pending_token": previous.pending_token,
            "pending_list": previous.pending_list,
        }
    elif entry.list is not None and by_user is not None and entry.list.archived_at is None:
        invite = ListInviteToken.objects.create(
            list=entry.list,
            invited_by=by_user,
            target_email=person.email,
            target_person=person,
            mode=entry.list.template.member_subject_mode,
        )
        grant = {
            "pending_key": "pending_invite_token",
            "pending_token": invite.token,
            "pending_list": entry.list,
        }
    elif entry.list is not None:
        grant = {"pending_list": entry.list}

    with transaction.atomic():
        token = ActivationToken.issue(
            person=person, email=person.email, user=entry.user, **grant
        )
    entry.user.activate_tokens = [token, *entry.user.activate_tokens]
    link = f"{(settings.RP_ORIGIN or '').rstrip('/')}{reverse('accounts:activate', args=[token.token])}"
    try:
        send_mail(
            subject="Fichtelink — Konto aktivieren (neuer Link)",
            message=_body(person.given_name, link, token, joins=bool(grant.get("pending_key"))),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[person.email],
        )
    except (smtplib.SMTPException, OSError) as exc:
        log.error("resend activation to %s failed: %s", person.email, exc)
        return False
    log.info("activation re-sent: user=%s by=%s", entry.user.pk, by_user.pk if by_user else "cli")
    return True


def _body(given_name: str, link: str, token: ActivationToken, *, joins: bool) -> str:
    body = (
        f"Hallo {given_name},\n\n"
        f"Sie haben sich bei Fichtelink registriert, Ihr Konto ist aber noch nicht "
        f"aktiviert. Mit diesem neuen Link schließen Sie die Aktivierung ab:\n\n"
        f"{link}\n\n"
        f"Der Link ist bis {timezone.localtime(token.expires_at):%d.%m.%Y %H:%M} gültig.\n\n"
        f"Falls Ihr Gerät keinen Passkey einrichten kann, klicken Sie auf der "
        f"Seite auf „Passkey klappt nicht?“. Dort finden Sie Tipps und können "
        f"Fichtelink auch ohne Passkey nutzen — die Anmeldung läuft dann über einen "
        f"Link per E-Mail."
    )
    if not joins:
        body += (
            "\n\nFalls Sie nach der Aktivierung nicht automatisch in Ihrer Klasse "
            "landen, öffnen Sie den QR-Code bzw. Einladungslink der Klasse einfach "
            "noch einmal."
        )
    return body
