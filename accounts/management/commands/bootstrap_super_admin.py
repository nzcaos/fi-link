"""Create the initial super-admin and print a passkey-enrollment link.

Activation flow is the same as for regular users: no password is set, the
operator clicks the printed link in a browser and enrolls a passkey. See
CLAUDE.md / "Super-admin bootstrap".
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse

from accounts.models import ActivationToken, Person, User
from accounts.webauthn_service import random_username


class Command(BaseCommand):
    help = "Create a super-admin User+Person and print a passkey-enrollment link."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="Super-admin's email address.")
        parser.add_argument("--given-name", required=True)
        parser.add_argument("--family-name", required=True)
        parser.add_argument(
            "--base-url",
            default="https://fichtelink.caos.cloud",
            help="Base URL used to build the activation link (production hostname).",
        )

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        given_name = options["given_name"].strip()
        family_name = options["family_name"].strip()
        base_url = options["base_url"].rstrip("/")

        if User.objects.filter(is_superuser=True).exists():
            raise CommandError(
                "A super-admin already exists. Use --add-additional or the admin UI "
                "to grant another user staff/superuser status."
            )

        with transaction.atomic():
            person = Person.objects.create(
                given_name=given_name, family_name=family_name, email=email
            )
            user = User.objects.create_user(
                person=person,
                username=random_username(),
                is_active=False,
                is_staff=True,
                is_superuser=True,
            )
            token = ActivationToken.issue(person=person, email=email, user=user)

        path = reverse("accounts:activate", args=[token.token])
        link = f"{base_url}{path}"

        self.stdout.write(self.style.SUCCESS("Super-admin angelegt."))
        self.stdout.write("")
        self.stdout.write(f"  Person:   {person.full_name} <{person.email}>")
        self.stdout.write(f"  User-ID:  {user.pk}")
        self.stdout.write(f"  Link:     {link}")
        self.stdout.write("")
        self.stdout.write(
            "Diesen Link öffnen, Passkey einrichten. Der Link ist 7 Tage gültig."
        )
