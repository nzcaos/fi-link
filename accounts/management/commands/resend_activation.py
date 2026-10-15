"""Mail a fresh activation link to accounts that were never activated.

The same list and re-send as the admins' "Nicht abgeschlossene Anmeldungen"
pages (`lists.pending_registrations`), for the operator shell. From here the
new link only keeps the previous invitation while it is still usable — a new
class invitation needs an admin as inviter, so that is the web page's job.

Without --send it only lists the accounts.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from lists.pending_registrations import pending_registrations, resend_activation


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
        entries = pending_registrations()
        if options["user_ids"]:
            entries = [e for e in entries if e.user.pk in options["user_ids"]]
        if not entries:
            self.stdout.write("Keine nie aktivierten Konten gefunden.")
            return

        sent = 0
        for entry in entries:
            line = (
                f"#{entry.user.pk}  {entry.person.full_name} <{entry.person.email}>  "
                f"Klasse: {entry.list.title if entry.list else 'unbekannt'}"
            )
            if not options["send"]:
                self.stdout.write(line)
                continue
            if resend_activation(entry):
                sent += 1
                self.stdout.write(f"gesendet  {line}")
            else:
                self.stderr.write(f"FEHLER    {line} (siehe Log)")

        if options["send"]:
            self.stdout.write(self.style.SUCCESS(f"{sent} von {len(entries)} Mails versendet."))
        else:
            self.stdout.write("")
            self.stdout.write(f"{len(entries)} Konto/Konten. Mit --send verschicken.")
