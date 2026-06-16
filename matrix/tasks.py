"""Procrastinate tasks owned by the matrix app.

Discovery: `procrastinate.contrib.django` auto-imports a `tasks` submodule from
every INSTALLED_APP, so this file only needs to exist. Account provisioning is
exposed as a task for the event-driven path (Phase 4: enqueue on list join) so
the Synapse call is retried on transient failure; the credentials view calls
`ensure_matrix_account` directly because it needs the result to display.
"""
from __future__ import annotations

import logging

from django.conf import settings
from procrastinate.contrib.django import app

log = logging.getLogger(__name__)


@app.task(name="matrix.provision_account", pass_context=False)
def provision_matrix_account(user_id: int) -> None:
    if not settings.MATRIX_ENABLED:
        return
    from accounts.models import User

    from .service import ensure_matrix_account

    user = User.objects.get(pk=user_id)
    ensure_matrix_account(user)
