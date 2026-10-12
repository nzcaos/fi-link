"""Erase a PERSON (and USER) on request — see `lists/erasure.py`.

Dry-run by default: prints what would be deleted. Pass `--yes` to execute.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Person, User
from lists.erasure import execute_erasure, plan_erasure


class Command(BaseCommand):
    help = (
        "Delete a person with account, list records (incl. archived), orphaned "
        "children and mail metadata. Dry-run unless --yes is given."
    )

    def add_arguments(self, parser):
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--email", help="Email on the Person record.")
        target.add_argument("--person-id", type=int, help="Person primary key.")
        parser.add_argument(
            "--reassign-to",
            type=int,
            help=(
                "User id that takes over audit references (invitations sent, "
                "forms created, …). Default: the first super-admin not being erased."
            ),
        )
        parser.add_argument(
            "--keep-children",
            action="store_true",
            help="Do not erase children whose only guardians are being erased.",
        )
        parser.add_argument("--yes", action="store_true", help="Actually delete.")

    def handle(self, *args, **options):
        person = self._resolve_person(options)
        plan = plan_erasure(person, include_children=not options["keep_children"])

        self.stdout.write("Zu löschende Personen:")
        for p in plan.persons:
            has_user = any(u.person_id == p.pk for u in plan.users)
            self.stdout.write(
                f"  #{p.pk}  {p.full_name} <{p.email or '—'}>"
                f"{'  [Konto]' if has_user else ''}"
            )
        self.stdout.write(f"Listeneinträge (inkl. archivierter): {plan.record_count}")
        self.stdout.write(
            "Mail-Daten/Tokens für: " + (", ".join(plan.emails) or "— (Adresse wird weiter genutzt)")
        )
        for label, n in plan.protected_refs.items():
            self.stdout.write(f"Übergabe an Ersatz-Benutzer: {label} × {n}")
        for lst in plan.sole_admin_lists:
            self.stdout.write(
                self.style.WARNING(f"Achtung: Liste '{lst.title}' verliert ihren einzigen Admin.")
            )

        reassign_to = self._resolve_reassign(options, plan) if plan.protected_refs else None

        if not options["yes"]:
            self.stdout.write("")
            self.stdout.write("Trockenlauf — nichts gelöscht. Mit --yes ausführen.")
            return

        counts = execute_erasure(plan, reassign_to=reassign_to)
        self.stdout.write("")
        for label, n in sorted(counts.items()):
            self.stdout.write(f"  {label}: {n}")
        self.stdout.write(self.style.SUCCESS("Gelöscht."))
        self.stdout.write(
            "Verbleibend: Warteschlangen-Jobs (automatisch nach TASK_JOB_RETENTION_DAYS), "
            "Container-Logs (Rotation), Backups (KEEP_DAYS)."
        )

    def _resolve_person(self, options) -> Person:
        if options["person_id"] is not None:
            person = Person.objects.filter(pk=options["person_id"]).first()
            if person is None:
                raise CommandError(f"Keine Person #{options['person_id']}.")
            return person
        email = options["email"].strip()
        matches = list(Person.objects.filter(email__iexact=email))
        if not matches:
            raise CommandError(f"Keine Person mit E-Mail '{email}'.")
        if len(matches) > 1:
            self.stdout.write("Mehrere Treffer — bitte mit --person-id eingrenzen:")
            for p in matches:
                self.stdout.write(f"  #{p.pk}  {p.full_name}")
            raise CommandError("Mehrdeutig.")
        return matches[0]

    def _resolve_reassign(self, options, plan) -> User:
        erased = {u.pk for u in plan.users}
        if options["reassign_to"] is not None:
            user = User.objects.filter(pk=options["reassign_to"]).first()
            if user is None or user.pk in erased:
                raise CommandError("--reassign-to: unbekannter oder zu löschender Benutzer.")
        else:
            user = (
                User.objects.filter(is_superuser=True, is_active=True)
                .exclude(pk__in=erased)
                .order_by("pk")
                .first()
            )
            if user is None:
                raise CommandError("Kein Super-Admin als Ersatz gefunden — --reassign-to angeben.")
        self.stdout.write(f"Ersatz-Benutzer: #{user.pk} {user.person.full_name}")
        return user
