"""Mail a fresh activation link to accounts that were never activated.

For stubs whose registration stalled at the passkey step (device without
passkey support, lost mail): their links expire after 7 days. The new link
keeps the invitation (class join) of the stub's latest activation link, if
that token still exists, and the mail points out the way without a passkey
(CLAUDE.md / "Login by mail link").

Without --send it only lists the accounts.
"""
from __future__ import annotations

import smtplib

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from accounts.models import ActivationToken, User


class Command(BaseCommand):
    help = (
        "Neuen Aktivierungslink an nie aktivierte Konten (kein Passkey, nie "
        "angemeldet) senden. Ohne --send nur auflisten."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--send", action="store_true", help="Mails wirklich verschicken."
        )
        parser.add_argument(
            "--user-id",
            type=int,
            action="append",
            dest="user_ids",
            help="Nur diese User-ID (mehrfach angebbar).",
        )

    def handle(self, *args, **options):
        stubs = (
            User.objects.select_related("person")
            .filter(is_active=False, last_login__isnull=True, passkeys__isnull=True)
            .exclude(person__email__isnull=True)
            .exclude(person__email__exact="")
            .order_by("pk")
        )
        if options["user_ids"]:
            stubs = stubs.filter(pk__in=options["user_ids"])
        stubs = list(stubs)
        if not stubs:
            self.stdout.write("Keine nie aktivierten Konten gefunden.")
            return

        origin = (settings.RP_ORIGIN or "").rstrip("/")
        sent = 0
        for user in stubs:
            person = user.person
            previous = (
                ActivationToken.objects.filter(user=user, purpose=ActivationToken.Purpose.ACTIVATE)
                .order_by("-pk")
                .first()
            )
            grant = (
                {"pending_key": previous.pending_key, "pending_token": previous.pending_token}
                if previous is not None
                else {}
            )
            invitation = grant.get("pending_key") or "keine (Token schon gelöscht)"
            line = f"#{user.pk}  {person.full_name} <{person.email}>  Einladung: {invitation}"
            if not options["send"]:
                self.stdout.write(line)
                continue

            token = ActivationToken.issue(person=person, email=person.email, user=user, **grant)
            link = f"{origin}{reverse('accounts:activate', args=[token.token])}"
            try:
                send_mail(
                    subject="Fichtelink — Konto aktivieren (neuer Link)",
                    message=_body(
                        person.given_name, link, token, has_invitation=bool(grant.get("pending_key"))
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[person.email],
                )
            except (smtplib.SMTPException, OSError) as exc:
                self.stderr.write(f"FEHLER {line}: {exc}")
                continue
            sent += 1
            self.stdout.write(f"gesendet  {line}")

        if options["send"]:
            self.stdout.write(self.style.SUCCESS(f"{sent} von {len(stubs)} Mails versendet."))
        else:
            self.stdout.write("")
            self.stdout.write(f"{len(stubs)} Konto/Konten. Mit --send verschicken.")


def _body(given_name: str, link: str, token: ActivationToken, *, has_invitation: bool) -> str:
    body = (
        f"Hallo {given_name},\n\n"
        f"Sie haben sich bei Fichtelink registriert, Ihr Konto ist aber noch nicht "
        f"aktiviert. Mit diesem neuen Link schließen Sie die Aktivierung ab:\n\n"
        f"{link}\n\n"
        f"Der Link ist bis {timezone.localtime(token.expires_at):%d.%m.%Y %H:%M} gültig.\n\n"
        f"Neu: Falls Ihr Gerät keinen Passkey einrichten kann, klicken Sie auf der "
        f"Seite auf „Passkey klappt nicht?“. Dort finden Sie Tipps und können "
        f"Fichtelink auch ohne Passkey nutzen — die Anmeldung läuft dann über einen "
        f"Link per E-Mail."
    )
    if not has_invitation:
        body += (
            "\n\nFalls Sie nach der Aktivierung nicht automatisch in Ihrer Klasse "
            "landen, öffnen Sie den QR-Code bzw. Einladungslink der Klasse einfach "
            "noch einmal."
        )
    return body
