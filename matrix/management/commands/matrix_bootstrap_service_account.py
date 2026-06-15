"""Create the privileged Matrix service account our server acts as.

Run once after Synapse is up (Phase 0 step 8). Registers an admin account via
the shared-secret API and stores its access token (encrypted) in the singleton
MatrixServiceAccount row. Every later client-server call (create room, invite,
send, kick, ban) uses this token. The password is discarded after registration
— we only keep the long-lived access token (A4). No secrets are printed.
"""
from __future__ import annotations

import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from matrix.client import MatrixClient, MatrixError
from matrix.models import MatrixServiceAccount


class Command(BaseCommand):
    help = "Register the Matrix service account and store its access token."

    def add_arguments(self, parser):
        parser.add_argument(
            "--localpart",
            default="fichtelink-service",
            help="Localpart of the service account (→ @<localpart>:<server_name>).",
        )
        parser.add_argument(
            "--displayname",
            default="Fichtelink (Service)",
            help="Display name shown in rooms for the service account.",
        )

    def handle(self, *args, **options):
        if not settings.MATRIX_ENABLED:
            raise CommandError("MATRIX_ENABLED is False. Enable Matrix before bootstrapping.")
        if not settings.MATRIX_ADMIN_SHARED_SECRET:
            raise CommandError("MATRIX_ADMIN_SHARED_SECRET is empty — set it in .env first.")
        if MatrixServiceAccount.objects.exists():
            existing = MatrixServiceAccount.get()
            raise CommandError(
                f"A service account already exists ({existing.matrix_user_id}). "
                "Delete the row in the Django admin to re-bootstrap."
            )

        localpart = options["localpart"].strip()
        displayname = options["displayname"].strip()
        client = MatrixClient.from_settings()
        password = secrets.token_urlsafe(32)

        try:
            result = client.register_user(localpart, password, displayname, admin=True)
        except MatrixError as exc:
            if exc.errcode == "M_USER_IN_USE":
                raise CommandError(
                    f"@{localpart}:{client.server_name} already exists on Synapse but "
                    "there is no local MatrixServiceAccount row. The admin register API "
                    "cannot recover an existing account's token — either pick a fresh "
                    "--localpart, or reset that user's password via the Synapse admin API "
                    "and insert the token manually."
                ) from exc
            raise CommandError(f"Service-account registration failed: {exc}") from exc

        account = MatrixServiceAccount.objects.create(
            matrix_user_id=result["user_id"],
            access_token=result["access_token"],
            device_id=result.get("device_id", ""),
        )

        self.stdout.write(self.style.SUCCESS("Matrix service account created."))
        self.stdout.write(f"  Matrix-ID: {account.matrix_user_id}")
        self.stdout.write(f"  Device-ID: {account.device_id or '(none)'}")
        self.stdout.write("  Access token stored (encrypted). Not printed.")
