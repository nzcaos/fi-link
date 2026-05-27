"""Synchronous helpers used by the Phase 5a IMAP IDLE daemon.

The daemon itself (lists/management/commands/imap_idle_daemon.py) is async
because aioimaplib is. Everything that touches the ORM lives here and is
called via `asgiref.sync.sync_to_async` so the streaming socket is never
blocked by a sync DB roundtrip.

Per CLAUDE.md / "Long-lived IMAP IDLE consumer" the daemon performs **no**
business logic — its only responsibilities are:

  1. Persist one InboundMessage row per IMAP UID, idempotent across reconnects
     (UNIQUE on (UIDVALIDITY, UID) catches double-fetch races).
  2. Attach trivial structural metadata: which List/OutboundMessage does the
     local-part of the recipient address match, if any.
  3. Enqueue `lists.process_inbound` for Phase 5b's decision algorithm.

All real decisions (anti-loop suppression, member release, admin approval,
DSN correlation) are Phase 5b's job. We deliberately do NOT set
`decision = UNKNOWN_ALIAS` here even when nothing matches — keeping every
fresh row at PENDING gives Phase 5b a single entry shape to reason about.
"""
from __future__ import annotations

import email
import email.policy
import logging
import re
from datetime import datetime
from email.utils import getaddresses

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import InboundMessage, List, OutboundMessage

log = logging.getLogger(__name__)


_BOUNCE_PREFIX = "bounce-"
_ALIAS_PREFIX = "alias-"


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


def _decode_header(value: str | None) -> str:
    """Decode RFC 2047 encoded-words. `email.policy.default` already does this
    when we use `message_from_bytes(..., policy=email.policy.default)`, so
    callers receive str. This wrapper just normalises None → "".
    """
    return (value or "").strip()


def _parse_first_address(value: str | None) -> str:
    """Extract the bare address from a From: / Sender: header value.

    `getaddresses` is RFC-2822-tolerant. We return "" rather than raising
    because malformed headers reach us routinely (spam, broken MUAs) and
    Phase 5a's job is to persist regardless — Phase 5b can drop garbage rows
    via its decision algorithm.
    """
    if not value:
        return ""
    parsed = getaddresses([value])
    for _, addr in parsed:
        addr = (addr or "").strip()
        if addr:
            return addr
    return ""


def _strip_plus_tag(local: str) -> str:
    """RFC 5233 sub-addressing: `foo+bar@domain` routes to `foo`. Stripping
    keeps our alias matching robust against tagged variants users / forwarders
    might introduce.
    """
    return local.split("+", 1)[0]


def _normalise_local(local: str) -> str:
    return _strip_plus_tag(local).strip().lower()


def extract_recipient_alias(
    msg: email.message.Message, our_domain: str
) -> tuple[str, str]:
    """Pick the first recipient address whose domain matches ours. Returns
    `(local_part, domain)`, both lower-cased and stripped of plus-tags.

    Scans `To`, `Cc`, `Delivered-To`, `X-Original-To`, `Envelope-To` headers
    in that order. The catch-all mailbox can receive mail addressed to many
    aliases on our domain via BCC or forwarding, so the most reliable signal
    is whichever header records the envelope recipient — provider-specific:
    most expose `Delivered-To`, some `X-Original-To`.

    If no recipient on our domain is found, returns the first parseable
    address (lowercased) as a best-effort fallback.
    """
    our_domain_lc = our_domain.lower()
    headers = (
        "Delivered-To",
        "X-Original-To",
        "Envelope-To",
        "To",
        "Cc",
    )
    candidates: list[tuple[str, str]] = []
    for header in headers:
        for value in msg.get_all(header) or []:
            for _, addr in getaddresses([value]):
                if not addr or "@" not in addr:
                    continue
                local, _, domain = addr.partition("@")
                candidates.append((_normalise_local(local), domain.strip().lower()))

    for local, domain in candidates:
        if domain == our_domain_lc:
            return local, domain

    if candidates:
        return candidates[0]
    return "", ""


# ---------------------------------------------------------------------------
# Alias → record correlation
# ---------------------------------------------------------------------------


def resolve_alias(
    local_part: str,
) -> tuple[List | None, OutboundMessage | None]:
    """Map a local-part to (matched_list, matched_outbound).

    Three structural cases (Phase 5a is purely structural — no decisions):

    * `<email_alias>` matches a list → matched_list set, matched_outbound None.
    * `bounce-<token>` or `alias-<token>` matches an OutboundMessage →
      matched_outbound set, matched_list None.
    * No match → both None. Phase 5b interprets this as 'unknown alias'.

    A local-part can in principle clash (e.g. somebody creates a List with
    `email_alias = "bounce-XYZ"`). Lists win — our own bounce/alias tokens
    are 22 url-safe characters, collisions with a user-picked alias are
    vanishingly unlikely, and admitting a List as the match here is the
    less-surprising outcome.
    """
    if not local_part:
        return None, None
    local = _normalise_local(local_part)

    matched_list = (
        List.objects.filter(email_alias__iexact=local, archived_at__isnull=True)
        .first()
    )
    if matched_list is not None:
        return matched_list, None

    for prefix in (_BOUNCE_PREFIX, _ALIAS_PREFIX):
        if local.startswith(prefix):
            token = local[len(prefix) :]
            outbound = OutboundMessage.objects.filter(alias_token=token).first()
            if outbound is not None:
                return None, outbound
            # Prefix matched but token did not — probably an expired/pruned
            # outbound row or a typo. Leave both None so Phase 5b records
            # `decision=UNKNOWN_ALIAS` with the local_part captured.
            break

    return None, None


# ---------------------------------------------------------------------------
# Top-level persistence
# ---------------------------------------------------------------------------


_MSGID_RE = re.compile(r"<([^<>]+)>")


def _strip_messageid_brackets(value: str) -> str:
    """`Message-ID: <foo@bar>` → `foo@bar`. We store unbracketed for
    consistency with how OutboundMessage.message_id is stored.
    """
    if not value:
        return ""
    match = _MSGID_RE.search(value)
    if match:
        return match.group(1).strip()
    return value.strip().strip("<>")


def parse_and_persist(
    raw_eml: bytes,
    *,
    uidvalidity: int,
    uid: int,
    internaldate: datetime | None = None,
) -> InboundMessage | None:
    """Persist one InboundMessage row from a raw RFC-822 byte blob.

    Returns the new row, or `None` if a row with the same (UIDVALIDITY, UID)
    already exists — Phase 5a's idempotency guarantee. After a successful
    insert, defers `lists.process_inbound` on commit so the row never gets
    enqueued without being visible to the worker.
    """
    from .tasks import process_inbound  # local — avoids import cycle at import time

    try:
        msg = email.message_from_bytes(raw_eml, policy=email.policy.default)
    except Exception as exc:
        # email.policy.default is forgiving but a truly malformed blob can
        # still raise. We log + persist with empty headers rather than drop:
        # losing inbound rows silently is the worst failure mode here.
        log.warning("parse_and_persist: bad MIME (uid=%d): %s", uid, exc)
        msg = email.message.Message()

    message_id = _strip_messageid_brackets(_decode_header(msg.get("Message-ID")))
    from_email = _parse_first_address(msg.get("From"))
    subject = _decode_header(msg.get("Subject"))
    to_local, to_domain = extract_recipient_alias(msg, settings.MAIL_DOMAIN)
    matched_list, matched_outbound = resolve_alias(to_local)
    received_at = internaldate or timezone.now()

    try:
        with transaction.atomic():
            row = InboundMessage.objects.create(
                imap_uidvalidity=uidvalidity,
                imap_uid=uid,
                message_id=message_id[:255],
                from_email=from_email[:320],
                to_alias=to_local[:200],
                to_domain=to_domain[:255],
                subject=subject[:998],
                raw_eml=raw_eml,
                received_at=received_at,
                matched_list=matched_list,
                matched_outbound=matched_outbound,
            )
    except IntegrityError:
        # Composite unique on (uidvalidity, uid) caught a re-insert. Common
        # after a reconnect-driven re-drain or two concurrent fallback polls.
        # The original row is already enqueued; nothing to do.
        log.debug(
            "parse_and_persist: dedup on (uidvalidity=%d, uid=%d)",
            uidvalidity,
            uid,
        )
        return None

    transaction.on_commit(lambda pk=row.pk: process_inbound.defer(inbound_id=pk))
    return row
