"""Procrastinate tasks owned by the lists app.

Phase 4 — Outbound mail. One OutboundMessage row per recipient is written
inside a Django transaction; deferral of the per-recipient SMTP task is
delayed until commit so a rolled-back caller cannot orphan tasks against
nonexistent rows (CLAUDE.md / "Architecture decisions (task queue)").

Discovery: `procrastinate.contrib.django` auto-imports a `tasks` submodule
from every INSTALLED_APP.
"""
from __future__ import annotations

import logging
from email.utils import make_msgid
from smtplib import SMTPException

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate.contrib.django import app

from .models import InboundMessage, List, OutboundMessage

log = logging.getLogger(__name__)


# Total send attempts including the first try. Procrastinate's RetryStrategy
# and our app-level "mark FAILED on terminal failure" branch share this; keep
# them in sync via the constant.
MAX_SEND_ATTEMPTS = 5


# ---------------------------------------------------------------------------
# Header / address helpers
# ---------------------------------------------------------------------------


def _sanitize_header_value(value: str, max_length: int = 200) -> str:
    """Collapse whitespace (incl. embedded CR/LF) and cap length — defense in
    depth against header-injection (cf. review M9 / lists/views.py).
    """
    return " ".join((value or "").split())[:max_length]


def alias_address(kind: str, token: str) -> str:
    """Build a `<kind>-<token>@<MAIL_DOMAIN>` address.

    kind ∈ {"bounce", "alias"}:
      - "bounce" → envelope-from for outbound; DSNs route back here and Phase
        5b correlates them against OutboundMessage.alias_token.
      - "alias"  → visible From: header for anonymised forwards. Replies to
        this address are routed back to the original sender by the reply-
        routing task (Phase 5b).
    """
    if kind not in ("bounce", "alias"):
        raise ValueError(f"unknown alias kind: {kind!r}")
    return f"{kind}-{token}@{settings.MAIL_DOMAIN}"


def list_address(list_obj: List) -> str:
    """The list's public mail address (`<email_alias>@<MAIL_DOMAIN>`)."""
    return f"{list_obj.email_alias}@{settings.MAIL_DOMAIN}"


def _list_id_header(list_obj: List) -> str:
    """RFC 2919 List-Id: a fixed identifier rooted in our mail domain.

    Format `<local.MAIL_DOMAIN>` keeps it stable across renames in the UI
    (the email_alias is the renamable thing) and globally unique.
    """
    return f"<{list_obj.email_alias}.{settings.MAIL_DOMAIN}>"


def _list_post_header(list_obj: List) -> str:
    return f"<mailto:{list_address(list_obj)}>"


def _list_unsubscribe_header(list_obj: List) -> str:
    """Pointer to the list detail page until Phase 6 wires a true self-removal
    endpoint. RFC 2369 only requires that the value is a well-formed URI in
    angle brackets; a deep-link satisfies OOO heuristics that key on the
    header's presence (CLAUDE.md / "Autoresponder / loop suppression").
    """
    origin = (settings.RP_ORIGIN or "").rstrip("/")
    return f"<{origin}/lists/{list_obj.pk}/>"


# ---------------------------------------------------------------------------
# Message construction
# ---------------------------------------------------------------------------


def build_outbound_email(message: OutboundMessage, body: str) -> EmailMessage:
    """Construct the EmailMessage for a single OutboundMessage row.

    Envelope-from (= SMTP MAIL FROM, used by Django's SMTP backend as the
    `from_email` value) is `bounce-<token>@<MAIL_DOMAIN>` so DSNs come back
    to a token we can correlate (CLAUDE.md / "Envelope-From / SRS"). The
    visible From: header — overridden via the `headers` dict, which Django's
    EmailMessage.message() honours — is either the original sender (default
    forward) or `alias-<token>@<MAIL_DOMAIN>` (anonymised forward).
    """
    visible_from = (
        alias_address("alias", message.alias_token)
        if message.anonymized_from
        else message.from_email
    )
    return EmailMessage(
        subject=message.subject,
        body=body,
        from_email=alias_address("bounce", message.alias_token),
        to=[message.recipient_email],
        headers={
            "Message-ID": f"<{message.message_id}>",
            "From": visible_from,
            "List-Id": _list_id_header(message.list),
            "List-Post": _list_post_header(message.list),
            "List-Unsubscribe": _list_unsubscribe_header(message.list),
            # RFC 3834: mark our own outbound as auto-generated so well-
            # behaved autoresponders skip it. Anti-loop on the inbound side
            # (Phase 5b) leans on this same header.
            "Auto-Submitted": "auto-generated",
            "Precedence": "list",
        },
    )


# ---------------------------------------------------------------------------
# Task: SMTP submission for a single OutboundMessage
# ---------------------------------------------------------------------------


@app.task(
    name="lists.send_outbound_message",
    queue="mail",
    retry=RetryStrategy(
        max_attempts=MAX_SEND_ATTEMPTS,
        exponential_wait=60,  # 60s, 120s, 240s, 480s, 960s
        retry_exceptions={SMTPException, ConnectionError, OSError},
    ),
)
def send_outbound_message(outbound_id: int, body: str) -> None:
    """SMTP-submit a single OutboundMessage. Idempotent on success (already-
    sent rows are skipped) so a retry that races a successful delivery cannot
    double-send.

    Failures: the row stays PENDING and the exception is re-raised so
    procrastinate's RetryStrategy schedules a retry. On the final attempt the
    row is flipped to FAILED so the admin UI can surface it (Phase 5b will
    add resend/cancel buttons).
    """
    om = OutboundMessage.objects.select_related("list").get(pk=outbound_id)
    if om.status == OutboundMessage.Status.SENT:
        return

    om.attempts += 1
    try:
        msg = build_outbound_email(om, body)
        msg.send(fail_silently=False)
    except SMTPException as exc:
        om.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        if om.attempts >= MAX_SEND_ATTEMPTS:
            om.status = OutboundMessage.Status.FAILED
        # else status stays PENDING for the next retry attempt.
        om.save(update_fields=["attempts", "status", "last_error"])
        log.warning(
            "send_outbound_message %d failed (attempt %d/%d): %s",
            om.pk,
            om.attempts,
            MAX_SEND_ATTEMPTS,
            exc,
        )
        raise
    else:
        om.status = OutboundMessage.Status.SENT
        om.sent_at = timezone.now()
        om.last_error = ""
        om.save(update_fields=["status", "sent_at", "attempts", "last_error"])


# ---------------------------------------------------------------------------
# Fan-out helper
# ---------------------------------------------------------------------------


def list_recipient_emails(list_obj: List) -> list[str]:
    """Resolve the list's Benutzergruppe to a deduped list of deliverable
    email addresses.

    Source: every USER in ListAccess whose PERSON has a non-empty email.
    PERSON.email may be shared across USERs (the family-mailbox case), so the
    same address can map to multiple members; SMTP-wise we send to it once.
    Admins are NOT auto-included — being an admin without a ListAccess row
    means "moderates the list but does not receive its mail".
    """
    from accounts.models import Person  # local — avoids import cycle

    emails = (
        Person.objects.filter(user__member_of_lists__list=list_obj)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .values_list("email", flat=True)
        .distinct()
    )
    return list(emails)


def enqueue_list_fanout(
    *,
    list_obj: List,
    from_email: str,
    subject: str,
    body: str,
    anonymize: bool = False,
) -> list[OutboundMessage]:
    """Create one OutboundMessage row per recipient and enqueue a send task
    per row. The task deferral runs on `transaction.on_commit`, so callers
    inside a rolled-back transaction do not produce orphan tasks.

    Returns the created rows (mainly useful for the test-send admin UI to
    report counts; callers in Phase 5b's forwarding path can ignore it).
    """
    safe_from = _sanitize_header_value(from_email, max_length=254)
    safe_subject = _sanitize_header_value(subject, max_length=998)

    created: list[OutboundMessage] = []
    for recipient in list_recipient_emails(list_obj):
        # email.utils.make_msgid returns "<id@domain>"; strip the brackets
        # since the column holds the raw value and we re-bracket in headers.
        msgid = make_msgid(domain=settings.MAIL_DOMAIN).strip("<>")
        om = OutboundMessage.objects.create(
            list=list_obj,
            message_id=msgid,
            from_email=safe_from,
            recipient_email=recipient,
            subject=safe_subject,
            anonymized_from=anonymize,
        )
        created.append(om)

    if created:
        ids = [om.pk for om in created]

        def _defer():
            for pk in ids:
                send_outbound_message.defer(outbound_id=pk, body=body)

        transaction.on_commit(_defer)
    return created


# ---------------------------------------------------------------------------
# Phase 5a: stub for the inbound pipeline (Phase 5b owns the real logic)
# ---------------------------------------------------------------------------


@app.task(
    name="lists.process_inbound",
    queue="mail",
    pass_context=False,
)
def process_inbound(inbound_id: int) -> None:
    """Phase 5a stub. The IMAP IDLE daemon enqueues this for every persisted
    InboundMessage; Phase 5b will fill in the decision algorithm
    (anti-loop checks, sender-is-member release flow, send-permission
    resolution, admin-approval routing, DSN correlation). Until then we
    log the receipt so the queue path is exercisable end-to-end.
    """
    try:
        msg = InboundMessage.objects.get(pk=inbound_id)
    except InboundMessage.DoesNotExist:
        log.warning("process_inbound: InboundMessage %d gone", inbound_id)
        return
    log.info(
        "process_inbound stub: pk=%d alias=%r from=%r decision=%s",
        msg.pk,
        msg.to_alias,
        msg.from_email,
        msg.decision,
    )
