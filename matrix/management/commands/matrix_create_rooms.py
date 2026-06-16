"""Create Matrix rooms for eligible class lists that don't have one yet.

Phase 3 / day-2 helper. Targets non-archived lists with matrix_room_enabled=True
and no MatrixRoom yet. With --verify it prints the key room state so the
operator can confirm join_rules / history_visibility / power_levels.

    docker compose exec web python manage.py matrix_create_rooms --verify
    docker compose exec web python manage.py matrix_create_rooms --list-id 12 --verify
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from lists.models import List
from matrix.client import MatrixClient, MatrixError
from matrix.models import MatrixRoom, MatrixServiceAccount
from matrix.service import ensure_room_for_list


class Command(BaseCommand):
    help = "Create Matrix rooms for eligible class lists missing one."

    def add_arguments(self, parser):
        parser.add_argument("--list-id", type=int, default=None, help="Only this list.")
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Print key room state (join_rules/history/power_levels) after creation.",
        )

    def handle(self, *args, **options):
        if not settings.MATRIX_ENABLED:
            raise CommandError("MATRIX_ENABLED is False.")

        qs = List.objects.filter(matrix_room_enabled=True, archived_at__isnull=True)
        if options["list_id"]:
            qs = qs.filter(pk=options["list_id"])

        client = MatrixClient.from_settings()
        service_account = MatrixServiceAccount.get()
        created = skipped = failed = 0

        for lst in qs:
            if MatrixRoom.objects.filter(list=lst).exists():
                skipped += 1
                continue
            try:
                room = ensure_room_for_list(lst)
            except MatrixError as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"list {lst.pk} «{lst.title}»: {exc}"))
                continue
            created += 1
            self.stdout.write(
                self.style.SUCCESS(f"created {room.room_id} for «{lst.title}» (list {lst.pk})")
            )
            if options["verify"] and service_account:
                self._print_state(client, service_account.access_token, room.room_id)

        self.stdout.write(f"Done. created={created}, skipped(existing)={skipped}, failed={failed}.")

    def _print_state(self, client, token, room_id):
        try:
            state = client.get_room_state(token, room_id)
        except MatrixError as exc:
            self.stderr.write(self.style.WARNING(f"    state verify failed: {exc}"))
            return
        by_type = {ev.get("type"): ev.get("content", {}) for ev in state}
        join = by_type.get("m.room.join_rules", {}).get("join_rule")
        hist = by_type.get("m.room.history_visibility", {}).get("history_visibility")
        pls = by_type.get("m.room.power_levels", {})
        self.stdout.write(
            f"    join_rule={join} history={hist} "
            f"invite_pl={pls.get('invite')} events_default={pls.get('events_default')}"
        )
