"""On-demand membership reconcile: invite Benutzergruppe members missing from
their class room. The same logic runs daily as a periodic task
(matrix.tasks.reconcile_all_rooms); this command is for ad-hoc repair.

    docker compose exec web python manage.py matrix_reconcile
    docker compose exec web python manage.py matrix_reconcile --list-id 12
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from lists.models import List
from matrix.client import MatrixError
from matrix.service import reconcile_list_room


class Command(BaseCommand):
    help = "Invite Benutzergruppe members who are missing from their class room."

    def add_arguments(self, parser):
        parser.add_argument("--list-id", type=int, default=None, help="Only this list.")

    def handle(self, *args, **options):
        if not settings.MATRIX_ENABLED:
            raise CommandError("MATRIX_ENABLED is False.")

        qs = List.objects.filter(
            matrix_room_enabled=True, archived_at__isnull=True, matrix_room__isnull=False
        )
        if options["list_id"]:
            qs = qs.filter(pk=options["list_id"])

        total = 0
        for lst in qs:
            try:
                repaired = reconcile_list_room(lst)
            except MatrixError as exc:
                self.stderr.write(self.style.ERROR(f"list {lst.pk} «{lst.title}»: {exc}"))
                continue
            total += repaired
            if repaired:
                self.stdout.write(f"«{lst.title}» (list {lst.pk}): {repaired} repair invite(s)")
        self.stdout.write(self.style.SUCCESS(f"Done. {total} repair invite(s) issued."))
