"""Out-of-band recovery: delete a user's passkeys and issue an enrollment link.

This is the only path back into a locked-out account (see CLAUDE.md /
"Account recovery"). The operator runs this after manually verifying that
the requester is the legitimate user — usually via a phone call or in-
person check.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse

from accounts.models import ActivationToken, Passkey, Person, User


class Command(BaseCommand):
    help = (
        "Delete all passkeys for a User and print a fresh enrollment link. "
        "Use after verifying the request out-of-band."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            required=True,
            help="Email on the Person record. If multiple Users share the address, --user-id is required.",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            help="Disambiguate when --email matches multiple Users (shared family mailbox).",
        )
        parser.add_argument(
            "--base-url",
            default="https://fichtelink.caos.cloud",
            help="Base URL used to build the link.",
        )

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        user_id = options.get("user_id")
        base_url = options["base_url"].rstrip("/")

        users = User.objects.select_related("person").filter(person__email__iexact=email)
        if user_id is not None:
            users = users.filter(pk=user_id)

        users_list = list(users)
        if not users_list:
            raise CommandError(f"Keine Benutzer mit E-Mail '{email}' gefunden.")
        if len(users_list) > 1:
            self.stdout.write("Mehrere Treffer — bitte mit --user-id eingrenzen:")
            for u in users_list:
                self.stdout.write(f"  #{u.pk}  {u.person.full_name} <{u.person.email}>")
            raise CommandError("Mehrdeutig.")

        user = users_list[0]
        person = user.person

        with transaction.atomic():
            deleted, _ = Passkey.objects.filter(user=user).delete()
            token = ActivationToken.issue(
                person=person,
                email=person.email or email,
                user=user,
                purpose=ActivationToken.Purpose.RECOVER,
            )

        link = f"{base_url}{reverse('accounts:activate', args=[token.token])}"
        self.stdout.write(self.style.SUCCESS(f"{deleted} Passkey(s) entfernt."))
        self.stdout.write("")
        self.stdout.write(f"  Person: {person.full_name} <{person.email}>")
        self.stdout.write(f"  Link:   {link}")
        self.stdout.write("")
        self.stdout.write(
            "Diesen Link der Person zustellen (E-Mail, Telefon, persönlich). "
            "Sieben Tage gültig."
        )
