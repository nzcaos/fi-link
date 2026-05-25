"""Procrastinate tasks owned by the accounts app.

Discovery: `procrastinate.contrib.django` auto-imports a `tasks` submodule
from every INSTALLED_APP, so this file only needs to exist — no manual
registration required.
"""
from __future__ import annotations

import logging

from django.utils import timezone
from procrastinate.contrib.django import app

from .models import WebAuthnChallenge

log = logging.getLogger(__name__)


@app.periodic(cron="*/5 * * * *")
@app.task(queueing_lock="sweep_webauthn_challenges", pass_context=False)
def sweep_expired_webauthn_challenges(timestamp: int) -> None:
    """Delete WebAuthnChallenge rows whose TTL has passed.

    The TTL is 5 minutes (see CLAUDE.md / "Challenge storage"); this job
    runs at the same cadence so the table stays small. `queueing_lock`
    ensures at most one sweep is queued at a time.
    """
    deleted, _ = WebAuthnChallenge.objects.filter(expires_at__lt=timezone.now()).delete()
    if deleted:
        log.info("sweep_webauthn_challenges: removed %d rows", deleted)
