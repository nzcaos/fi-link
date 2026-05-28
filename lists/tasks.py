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
from datetime import timedelta
from email.utils import make_msgid
from smtplib import SMTPException

from django.conf import settings
from django.core.mail import EmailMessage, send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate.contrib.django import app

from .models import InboundMessage, List, MailReleaseToken, OutboundMessage

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
# Phase 5b: inbound decision pipeline
# ---------------------------------------------------------------------------


def _release_link(token: MailReleaseToken) -> str:
    """Absolute URL to the release/approval page. Built from RP_ORIGIN because
    tasks run without an HttpRequest to call build_absolute_uri on.
    """
    origin = (settings.RP_ORIGIN or "").rstrip("/")
    return origin + reverse("lists:mail_release", kwargs={"token": token.token})


def _list_admin_emails(list_obj: List) -> list[str]:
    """Deliverable email addresses of a list's admins (for admin-approval
    dispatch). Deduped; admins without an email are skipped.
    """
    from accounts.models import Person

    emails = (
        Person.objects.filter(user__admin_of_lists__list=list_obj)
        .exclude(email__isnull=True)
        .exclude(email__exact="")
        .values_list("email", flat=True)
        .distinct()
    )
    return list(emails)


@app.task(
    name="lists.send_notification_mail",
    queue="mail",
    retry=RetryStrategy(
        max_attempts=MAX_SEND_ATTEMPTS,
        exponential_wait=60,
        retry_exceptions={SMTPException, ConnectionError, OSError},
    ),
)
def send_notification_mail(recipients: list[str], subject: str, body: str) -> None:
    """Send a plain release/approval notification mail. Retryable so a flaky
    SMTP submission doesn't lose the link. Decoupled from process_inbound so a
    mail failure never re-runs the (non-idempotent-on-retry) decision logic.
    """
    recipients = [r for r in recipients if r]
    if not recipients:
        return
    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipients,
        fail_silently=False,
    )


def _mark(inbound: InboundMessage, decision: str, reason: str) -> None:
    inbound.decision = decision
    inbound.reason = reason[:2000]
    inbound.save(update_fields=["decision", "reason"])


def _handle_bounce(inbound: InboundMessage, msg) -> None:
    """DSN routed to a ``bounce-<token>@`` alias: flip the correlated outbound
    row to BOUNCED and record a human-readable reason. The token match (already
    resolved into ``matched_outbound`` by the consumer) is the correlation;
    DSN parsing only enriches the message.
    """
    from .inbound_pipeline import parse_dsn

    om = inbound.matched_outbound
    status, diagnostic = parse_dsn(msg)
    detail = " ".join(p for p in (status, diagnostic) if p) or "kein DSN-Detail"
    with transaction.atomic():
        if om is not None and om.status != OutboundMessage.Status.BOUNCED:
            om.status = OutboundMessage.Status.BOUNCED
            om.last_error = f"DSN: {detail}"[:2000]
            om.save(update_fields=["status", "last_error"])
        recipient = om.recipient_email if om is not None else "?"
        _mark(
            inbound,
            InboundMessage.Decision.BOUNCE,
            f"Bounce für {recipient}: {detail}",
        )
    log.info("process_inbound: bounce correlated inbound=%d outbound=%s",
             inbound.pk, om.pk if om else None)


def _route_reply(inbound: InboundMessage, msg) -> None:
    """Reply to an anonymisation alias (``alias-<token>@``): forward it back to
    the original sender of the mail that was anonymised. The replier's address
    is shown to the original sender (they are in a 1:1 exchange); the original
    sender's address was never exposed to the replier — that's the whole point
    of the alias. Reuses the OutboundMessage machinery for retry + bounce
    correlation.
    """
    from .inbound_pipeline import extract_subject_and_body

    om = inbound.matched_outbound
    original_sender = (om.from_email or "").strip() if om is not None else ""
    if not original_sender:
        _mark(
            inbound,
            InboundMessage.Decision.REJECTED,
            "Reply-Alias ohne auflösbaren Originalsender",
        )
        return

    subject, body = extract_subject_and_body(msg)
    with transaction.atomic():
        reply = OutboundMessage.objects.create(
            list=om.list,
            message_id=make_msgid(domain=settings.MAIL_DOMAIN).strip("<>"),
            from_email=(inbound.from_email or settings.DEFAULT_FROM_EMAIL),
            recipient_email=original_sender,
            subject=_sanitize_header_value(subject or "(Antwort)", max_length=998),
            anonymized_from=False,
        )
        _mark(
            inbound,
            InboundMessage.Decision.FORWARDED,
            f"Antwort an Originalsender geroutet (Alias {om.alias_token}).",
        )
    transaction.on_commit(
        lambda pk=reply.pk: send_outbound_message.defer(outbound_id=pk, body=body)
    )
    log.info("process_inbound: reply routed inbound=%d → %s", inbound.pk, original_sender)


def _create_release_token(
    inbound: InboundMessage,
    target: List,
    *,
    kind: str,
    offer_anonymize: bool,
    recipients: list[str],
    notify_subject: str,
    notify_body: str,
) -> None:
    """Persist a MailReleaseToken + flip the inbound to PENDING_APPROVAL in one
    transaction, then defer the notification mail on commit. The single-
    transaction write keeps process_inbound idempotent: if it ever re-runs, the
    top-of-task guard sees a non-PENDING decision and bails before duplicating.
    """
    recipients = [r for r in recipients if r]
    with transaction.atomic():
        token = MailReleaseToken.objects.create(
            inbound=inbound,
            list=target,
            kind=kind,
            offer_anonymize=offer_anonymize,
        )
        _mark(
            inbound,
            InboundMessage.Decision.PENDING_APPROVAL,
            f"{kind}-Freigabe ausstehend (Token {token.token[:8]}…).",
        )
    if recipients:
        link = _release_link(token)
        body = notify_body.replace("__LINK__", link)
        transaction.on_commit(
            lambda: send_notification_mail.defer(
                recipients=recipients, subject=notify_subject, body=body
            )
        )
    else:
        log.warning(
            "process_inbound: no recipients for %s release of inbound %d",
            kind,
            inbound.pk,
        )


def _dispatch_decision(inbound: InboundMessage, target: List, decision, msg) -> None:
    from .inbound_pipeline import Outcome, extract_subject_and_body

    if decision.outcome == Outcome.FORWARD:
        subject, body = extract_subject_and_body(msg)
        with transaction.atomic():
            enqueue_list_fanout(
                list_obj=target,
                from_email=inbound.from_email or settings.DEFAULT_FROM_EMAIL,
                subject=subject,
                body=body,
                anonymize=False,
            )
            _mark(
                inbound,
                InboundMessage.Decision.FORWARDED,
                "Direkt weitergeleitet (Senderecht, ohne Freigabe-Klick).",
            )
        log.info("process_inbound: direct forward inbound=%d list=%s",
                 inbound.pk, target.email_alias)
        return

    if decision.outcome in (Outcome.MEMBER_RELEASE, Outcome.PERMITTED_RELEASE):
        offer_anonymize = decision.outcome == Outcome.MEMBER_RELEASE
        _create_release_token(
            inbound,
            target,
            kind=MailReleaseToken.Kind.MEMBER,
            offer_anonymize=offer_anonymize,
            recipients=[inbound.from_email],
            notify_subject=f'Freigabe für Ihre Mail an „{target.title}"',
            notify_body=(
                f"Hallo,\n\n"
                f'Sie haben eine Mail an die Liste „{target.title}" gesendet.\n'
                f"Aus Sicherheitsgründen wird sie erst nach Ihrer Bestätigung "
                f"weitergeleitet.\n\n"
                f"Bitte klicken Sie zum Freigeben:\n__LINK__\n\n"
                f"Wenn Sie diese Mail nicht gesendet haben, ignorieren Sie "
                f"diese Nachricht — es wird nichts weitergeleitet.\n"
            ),
        )
        log.info("process_inbound: member release inbound=%d list=%s",
                 inbound.pk, target.email_alias)
        return

    # ADMIN_APPROVAL
    admin_emails = _list_admin_emails(target)
    _create_release_token(
        inbound,
        target,
        kind=MailReleaseToken.Kind.ADMIN,
        offer_anonymize=False,
        recipients=admin_emails,
        notify_subject=f'Freigabe nötig: Mail an „{target.title}"',
        notify_body=(
            f"Hallo,\n\n"
            f'{inbound.from_email or "(unbekannt)"} möchte eine Mail an die '
            f'Liste „{target.title}" senden, ist aber nicht berechtigt.\n'
            f'Betreff: {inbound.subject or "(kein Betreff)"}\n\n'
            f"Bitte entscheiden Sie über die Weiterleitung:\n__LINK__\n"
        ),
    )
    log.info("process_inbound: admin approval inbound=%d list=%s recipients=%d",
             inbound.pk, target.email_alias, len(admin_emails))


@app.task(name="lists.process_inbound", queue="mail", pass_context=False)
def process_inbound(inbound_id: int) -> None:
    """The Phase 5b decision pipeline. Enqueued once per persisted
    InboundMessage by the IMAP consumer.

    Idempotent: the top guard bails unless the row is still PENDING, and every
    branch flips the decision away from PENDING inside the same transaction as
    its side-effecting writes. Routing is by alias type first (bounce / reply
    aliases bypass suppression because DSNs legitimately carry the very signals
    suppression keys on), then suppression, then the list decision algorithm.
    """
    from .inbound_pipeline import decide, identify_sender_users, parse_message, suppression_reason

    try:
        inbound = InboundMessage.objects.select_related(
            "matched_list__template", "matched_outbound__list"
        ).get(pk=inbound_id)
    except InboundMessage.DoesNotExist:
        log.warning("process_inbound: InboundMessage %d gone", inbound_id)
        return

    if inbound.decision != InboundMessage.Decision.PENDING:
        log.info(
            "process_inbound: inbound=%d already decided (%s) — skip",
            inbound.pk,
            inbound.decision,
        )
        return

    msg = parse_message(bytes(inbound.raw_eml))
    alias = inbound.to_alias or ""

    # 1. Bounce/DSN → correlate against the originating outbound row.
    if alias.startswith("bounce-") and inbound.matched_outbound_id:
        _handle_bounce(inbound, msg)
        return

    # 2. Reply to an anonymisation alias → route back to the original sender.
    #    Suppress only on the OOO signals, NOT In-Reply-To (always matches here).
    if alias.startswith("alias-") and inbound.matched_outbound_id:
        reason = suppression_reason(msg, check_in_reply_to=False)
        if reason:
            _mark(inbound, InboundMessage.Decision.SUPPRESSED, reason)
            return
        _route_reply(inbound, msg)
        return

    # 3. List-addressed mail.
    if inbound.matched_list_id is None:
        _mark(
            inbound,
            InboundMessage.Decision.UNKNOWN_ALIAS,
            f"Kein Listen-/Outbound-Match für Alias {alias!r}.",
        )
        return

    target = inbound.matched_list
    if target.archived_at is not None:
        _mark(inbound, InboundMessage.Decision.REJECTED, "Liste ist archiviert.")
        return

    reason = suppression_reason(msg, check_in_reply_to=True)
    if reason:
        _mark(inbound, InboundMessage.Decision.SUPPRESSED, reason)
        return

    sender_users = list(identify_sender_users(inbound.from_email))
    decision = decide(target, sender_users)
    _dispatch_decision(inbound, target, decision, msg)


# ---------------------------------------------------------------------------
# Phase 5b: periodic maintenance tasks
# ---------------------------------------------------------------------------


@app.periodic(cron="17 3 * * *")
@app.task(queueing_lock="prune_mail_metadata", pass_context=False)
def prune_mail_metadata(timestamp: int) -> None:
    """Drop Inbound/Outbound metadata past the retention window (CLAUDE.md /
    "Retention", 30–90 days). Deleting an InboundMessage cascades its
    MailReleaseTokens; OutboundMessage deletes leave InboundMessage.
    matched_outbound NULL (SET_NULL).
    """
    cutoff = timezone.now() - timedelta(days=settings.MAIL_METADATA_RETENTION_DAYS)
    inbound_deleted, _ = InboundMessage.objects.filter(received_at__lt=cutoff).delete()
    outbound_deleted, _ = OutboundMessage.objects.filter(created_at__lt=cutoff).delete()
    if inbound_deleted or outbound_deleted:
        log.info(
            "prune_mail_metadata: removed %d inbound, %d outbound rows (< %s)",
            inbound_deleted,
            outbound_deleted,
            cutoff.date(),
        )


@app.periodic(cron="41 3 * * *")
@app.task(queueing_lock="expire_release_tokens", pass_context=False)
def expire_release_tokens(timestamp: int) -> None:
    """Delete release/approval tokens past their expiry. The InboundMessage
    keeps its decision for the audit trail; only the now-useless token row goes.
    """
    deleted, _ = MailReleaseToken.objects.filter(
        expires_at__lt=timezone.now()
    ).delete()
    if deleted:
        log.info("expire_release_tokens: removed %d expired token(s)", deleted)


@app.periodic(cron="23 4 * * *")
@app.task(queueing_lock="imap_expunge_processed", pass_context=False)
def imap_expunge_processed(timestamp: int) -> None:
    """EXPUNGE processed (\\Seen) catch-all messages older than the grace period
    (CLAUDE.md / "Retention", 7 days). Runs in the worker with a plain
    synchronous imaplib connection — separate from the async IDLE daemon, which
    only persists. A no-op when IMAP isn't configured (dev).
    """
    import imaplib

    if not settings.IMAP_HOST or not settings.IMAP_USER:
        return
    cutoff = (
        timezone.now() - timedelta(days=settings.IMAP_EXPUNGE_GRACE_DAYS)
    ).strftime("%d-%b-%Y")

    cls = imaplib.IMAP4_SSL if settings.IMAP_USE_SSL else imaplib.IMAP4
    client = cls(settings.IMAP_HOST, settings.IMAP_PORT)
    try:
        client.login(settings.IMAP_USER, settings.IMAP_PASS)
        client.select("INBOX")
        typ, data = client.search(None, "SEEN", "BEFORE", cutoff)
        if typ != "OK" or not data or not data[0]:
            return
        seq_nums = data[0].split()
        for num in seq_nums:
            client.store(num, "+FLAGS", "\\Deleted")
        client.expunge()
        log.info(
            "imap_expunge_processed: expunged %d processed message(s) older than %s",
            len(seq_nums),
            cutoff,
        )
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            log.debug("imap_expunge_processed: logout failed", exc_info=True)
