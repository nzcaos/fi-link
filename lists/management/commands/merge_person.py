"""Merge a duplicate PERSON into another — see `lists/merge.py`.

  manage.py merge_person --suggest              candidate pairs to review
  manage.py merge_person --from 73 --into 70    dry-run: what would happen
  manage.py merge_person --from 73 --into 70 --yes
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from accounts.models import Person
from lists.merge import MergeError, execute_merge, plan_merge, suggest_merges


class Command(BaseCommand):
    help = (
        "Merge a duplicate person (without account) into another person. "
        "Dry-run unless --yes is given; --suggest lists candidate pairs."
    )

    def add_arguments(self, parser):
        parser.add_argument("--suggest", action="store_true", help="List candidate pairs.")
        parser.add_argument("--from", type=int, dest="source", help="Person id to merge away (no account).")
        parser.add_argument("--into", type=int, dest="target", help="Person id that stays.")
        parser.add_argument("--yes", action="store_true", help="Actually merge.")

    def handle(self, *args, **options):
        if options["suggest"]:
            pairs = suggest_merges()
            for source, target, reason in pairs:
                self.stdout.write(
                    f"  --from {source.pk} --into {target.pk}   "
                    f"{source} <{source.email or '-'}> → {target} <{target.email or '-'}>"
                    f"{' [Konto]' if hasattr(target, 'user') else ''}  ({reason})"
                )
            self.stdout.write(f"{len(pairs)} Vorschläge. Jeden einzeln prüfen (Dry-Run ohne --yes).")
            return

        if options["source"] is None or options["target"] is None:
            raise CommandError("--from und --into angeben (oder --suggest).")
        try:
            source = Person.objects.get(pk=options["source"])
            target = Person.objects.get(pk=options["target"])
        except Person.DoesNotExist as exc:
            raise CommandError("Person nicht gefunden.") from exc
        try:
            plan = plan_merge(source, target)
        except MergeError as exc:
            raise CommandError(str(exc)) from exc

        for line in plan.lines:
            self.stdout.write(line)
        if not options["yes"]:
            self.stdout.write("Dry-Run — mit --yes ausführen.")
            return
        execute_merge(plan)
        self.stdout.write(self.style.SUCCESS(f"person={source.pk} in person={target.pk} zusammengeführt."))
