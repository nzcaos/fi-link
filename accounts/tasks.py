"""Procrastinate tasks owned by the accounts app.

Discovery: `procrastinate.contrib.django` auto-imports a `tasks` submodule
from every INSTALLED_APP, so this file only needs to exist — no manual
registration required.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from procrastinate.contrib.django import app

from .models import ActivationToken, WebAuthnChallenge

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


@app.periodic(cron="35 3 * * *")
@app.task(queueing_lock="sweep_activation_tokens", pass_context=False)
def sweep_activation_tokens(timestamp: int) -> None:
    """Delete activation / recovery / login tokens that expired or were
    consumed more than TOKEN_RETENTION_DAYS ago. Each row holds the address the
    link was mailed to; once unusable it is no longer needed.

    Kept: tokens of never-activated accounts. They record which class the
    stalled registration belongs to (`pending_list`) for the admins' "Nicht
    abgeschlossene Anmeldungen" page; the address is on the PERSON anyway.
    """
    cutoff = timezone.now() - timedelta(days=settings.TOKEN_RETENTION_DAYS)
    deleted, _ = (
        ActivationToken.objects.filter(Q(expires_at__lt=cutoff) | Q(consumed_at__lt=cutoff))
        .exclude(user__is_active=False, user__last_login__isnull=True)
        .delete()
    )
    if deleted:
        log.info("sweep_activation_tokens: removed %d rows", deleted)


@app.periodic(cron="5 4 * * *")
@app.task(queueing_lock="clear_expired_sessions", pass_context=False)
def clear_expired_sessions(timestamp: int) -> None:
    """Equivalent of `manage.py clearsessions`: Django never deletes expired
    database sessions on its own, so without this `django_session` grows
    forever.
    """
    engine = import_module(settings.SESSION_ENGINE)
    engine.SessionStore.clear_expired()
