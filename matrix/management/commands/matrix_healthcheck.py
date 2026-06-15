"""Probe the configured Synapse homeserver.

Used in the Phase 0 operator checklist to confirm Synapse answers on the
internal Docker network before any account/room work. Runs inside the `web`
container (`docker compose exec web python manage.py matrix_healthcheck`); it
talks to MATRIX_BASE_URL (http://synapse:8008), never the public hostname.
"""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from matrix.client import MatrixClient, MatrixError


class Command(BaseCommand):
    help = "Check that the Synapse homeserver answers (GET /_matrix/client/versions)."

    def handle(self, *args, **options):
        if not settings.MATRIX_ENABLED:
            self.stdout.write(
                self.style.WARNING(
                    "MATRIX_ENABLED is False — probing MATRIX_BASE_URL anyway, "
                    "but the rest of the app will not touch Matrix."
                )
            )
        client = MatrixClient.from_settings()
        self.stdout.write(f"Probing {client.base_url}/_matrix/client/versions …")
        try:
            result = client.versions()
        except MatrixError as exc:
            raise CommandError(f"Synapse health check failed: {exc}") from exc

        versions = result.get("versions", [])
        self.stdout.write(self.style.SUCCESS("Synapse is reachable."))
        self.stdout.write(f"  server_name: {client.server_name}")
        self.stdout.write(f"  supported client versions: {', '.join(versions)}")
