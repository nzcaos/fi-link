"""High-level Matrix operations (docs/matrix-implementation-plan.md, Phase 2+).

Idempotent, side-effecting helpers that the views, tasks and (later) the
membership-sync layer call. The raw HTTP lives in client.py; this module owns
the policy (pseudonymous localparts/displaynames, encryption, onboarding state).
"""
from __future__ import annotations

import logging
import secrets

from django.conf import settings

from .client import MatrixClient, MatrixError
from .models import MatrixAccount

log = logging.getLogger(__name__)


def _pseudonymous_displayname(localpart: str) -> str:
    """Non-revealing display name (A1: always pseudonym for the pilot).

    Klarname-bei-Zustimmung is deferred until the name-visibility coupling lands
    (matrix-architecture.md §4). The suffix keeps two parents distinguishable in
    a room without encoding any real identity.
    """
    suffix = localpart.rsplit("-", 1)[-1][-4:]
    return f"Elternteil {suffix}"


def ensure_matrix_account(user) -> MatrixAccount:
    """Return the user's MatrixAccount, provisioning it on first use.

    Idempotent: a second call returns the existing row without touching Synapse.
    The localpart is non-speaking (`u-<hex>`, lowercase — Matrix ids must be
    lowercase) and re-rolled on the rare collision. The server-assigned password
    is stored encrypted so the credentials page can re-display it (Weg A).
    """
    if not settings.MATRIX_ENABLED:
        raise MatrixError("Matrix-Integration ist deaktiviert (MATRIX_ENABLED=False).")

    existing = MatrixAccount.objects.filter(user=user).first()
    if existing:
        return existing

    client = MatrixClient.from_settings()
    password = secrets.token_urlsafe(18)
    last_exc: MatrixError | None = None
    for _ in range(5):
        localpart = "u-" + secrets.token_hex(4)
        displayname = _pseudonymous_displayname(localpart)
        try:
            result = client.register_user(localpart, password, displayname, admin=False)
        except MatrixError as exc:
            if exc.errcode == "M_USER_IN_USE":
                last_exc = exc
                continue  # collision on the random localpart — re-roll
            raise
        account = MatrixAccount.objects.create(
            user=user,
            matrix_user_id=result["user_id"],
            password=password,
            onboarding_status=MatrixAccount.Status.CREATED,
        )
        log.info("matrix: provisioned account %s for user %s", account.matrix_user_id, user.pk)
        return account

    raise MatrixError("Kein freier Matrix-Localpart nach mehreren Versuchen.") from last_exc
