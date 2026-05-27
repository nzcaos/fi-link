"""`python manage.py imap_idle_daemon` — long-lived IMAP IDLE consumer.

Supervised by the `imap_idle` compose service. All logic lives in
`lists.imap_consumer`; this file is a thin glue layer so the consumer is
importable / unit-testable independently of Django's command machinery.
"""
from __future__ import annotations

import asyncio
import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from lists.imap_consumer import main

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run the IMAP IDLE consumer for the catch-all mailbox. Persists one "
        "InboundMessage row per UID and enqueues `lists.process_inbound`."
    )

    def handle(self, *args, **options) -> None:
        if not settings.IMAP_HOST or not settings.IMAP_USER:
            raise CommandError(
                "IMAP_HOST / IMAP_USER are not configured. Set them in .env "
                "and rebuild the imap_idle container."
            )
        log.info(
            "imap_idle: starting (host=%s port=%d ssl=%s user=%s)",
            settings.IMAP_HOST,
            settings.IMAP_PORT,
            settings.IMAP_USE_SSL,
            settings.IMAP_USER,
        )
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            log.info("imap_idle: KeyboardInterrupt — exiting")
