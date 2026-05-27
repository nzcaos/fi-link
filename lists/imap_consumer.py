"""Async IMAP IDLE consumer for the Phase 5a inbound pipeline.

Per CLAUDE.md / "Long-lived IMAP IDLE consumer": this is its own dedicated
process. Django does not ship a long-lived connection model, and procrastinate
workers are tuned for discrete tasks — neither is a fit for an IDLE socket
that must stay open for ~30 min between server pings. The consumer therefore
runs as an asyncio daemon, supervised by the `imap_idle` compose service,
and pushes all retryable / correctness-critical work into the procrastinate
queue.

Responsibilities (strict):

* Open one IMAP connection to the catch-all mailbox.
* SELECT INBOX, learn UIDVALIDITY.
* On startup: drain any UNSEEN UIDs (we may have been offline).
* Enter IDLE. Wake on either:
    - server push (EXISTS / RECENT / EXPUNGE) → re-drain
    - `IMAP_FALLBACK_POLL_INTERVAL` elapsed → re-drain anyway (covers missed
      pushes from flaky networks / NAT timeouts / half-broken proxies)
    - `IMAP_IDLE_TIMEOUT` elapsed → DONE + re-IDLE so the server's 30-min
      kick never lands while we're blocked.
* On connection drop / unexpected exception: outer loop reconnects with
  exponential backoff (capped at 5 min).
* On SIGTERM / SIGINT: graceful logout + exit.

Idempotency is owned by `lists.inbound.parse_and_persist` via the composite
unique key (UIDVALIDITY, UID) on InboundMessage — re-fetching an already-
seen UID after reconnect is a no-op.
"""
from __future__ import annotations

import asyncio
import logging
import re
import signal
import time
from datetime import datetime
from typing import Optional

import aioimaplib
from asgiref.sync import sync_to_async
from django.conf import settings

from .inbound import parse_and_persist
from .models import InboundMessage

log = logging.getLogger(__name__)


# Outer reconnect backoff: 1s, 2s, 4s, 8s, …, capped.
_BACKOFF_INITIAL = 1.0
_BACKOFF_CAP = 300.0


# ---------------------------------------------------------------------------
# Helpers — low-level IMAP parsing
# ---------------------------------------------------------------------------


_UIDVALIDITY_RE = re.compile(rb"UIDVALIDITY\s+(\d+)", re.IGNORECASE)
_INTERNALDATE_RE = re.compile(rb'INTERNALDATE\s+"([^"]+)"', re.IGNORECASE)
_LITERAL_RE = re.compile(rb"\{(\d+)\}")


def _parse_internaldate(value: str) -> Optional[datetime]:
    """IMAP INTERNALDATE format: `"01-Jan-2026 12:34:56 +0000"`. Python's
    `strptime` chokes on single-digit days on some platforms (no `%-d`)
    — strip a leading space if present.
    """
    cleaned = value.replace("  ", " ").strip()
    if cleaned.startswith(" "):
        cleaned = cleaned.lstrip()
    if cleaned and not cleaned[0].isdigit():
        return None
    if len(cleaned) > 2 and cleaned[1] == "-":
        cleaned = "0" + cleaned  # normalise "1-Jan-..." → "01-Jan-..."
    try:
        return datetime.strptime(cleaned, "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        log.debug("could not parse INTERNALDATE %r", value)
        return None


def _parse_fetch_response(
    data: list[bytes],
) -> tuple[Optional[bytes], Optional[datetime]]:
    """Extract `(raw_eml, internaldate)` from a UID FETCH response payload.

    aioimaplib returns the FETCH data as a list of bytes blobs, in this
    typical shape:

        [b'1 FETCH (UID 5 INTERNALDATE "..." BODY[] {12345}',
         b'<12345 bytes of RFC-822 body>',
         b')']

    We pick the body as the bytes blob immediately following a `{N}`
    literal marker, and read INTERNALDATE off whichever line carries it.
    """
    raw_eml: Optional[bytes] = None
    internaldate: Optional[datetime] = None

    for index, entry in enumerate(data):
        if not isinstance(entry, (bytes, bytearray)):
            continue
        blob = bytes(entry)
        m_idate = _INTERNALDATE_RE.search(blob)
        if m_idate and internaldate is None:
            internaldate = _parse_internaldate(
                m_idate.group(1).decode("ascii", "replace")
            )
        m_lit = _LITERAL_RE.search(blob)
        if m_lit and raw_eml is None and index + 1 < len(data):
            candidate = data[index + 1]
            if isinstance(candidate, (bytes, bytearray)):
                raw_eml = bytes(candidate)
    return raw_eml, internaldate


# ---------------------------------------------------------------------------
# DB helpers (sync) wrapped for async use
# ---------------------------------------------------------------------------


def _already_persisted(uidvalidity: int, uid: int) -> bool:
    return InboundMessage.objects.filter(
        imap_uidvalidity=uidvalidity, imap_uid=uid
    ).exists()


_already_persisted_async = sync_to_async(_already_persisted, thread_sensitive=True)
_parse_and_persist_async = sync_to_async(parse_and_persist, thread_sensitive=True)


# ---------------------------------------------------------------------------
# Session flow
# ---------------------------------------------------------------------------


def _decode_imap_reason(data) -> str:
    """Join an aioimaplib response payload into a printable string. The server's
    reason for a NO/BAD response usually lives here — losing it makes failures
    much harder to diagnose than they need to be.
    """
    joined = b" ".join(b for b in data if isinstance(b, (bytes, bytearray)))
    return joined.decode("ascii", "replace")


async def _connect(host: str, port: int, use_ssl: bool, timeout: float):
    """Open and authenticate the IMAP connection."""
    cls = aioimaplib.IMAP4_SSL if use_ssl else aioimaplib.IMAP4
    client = cls(host=host, port=port, timeout=timeout)
    await client.wait_hello_from_server()
    status, data = await client.login(settings.IMAP_USER, settings.IMAP_PASS)
    if status != "OK":
        raise RuntimeError(
            f"IMAP LOGIN refused: {status} {_decode_imap_reason(data)!r}"
        )
    status, data = await client.select("INBOX")
    if status != "OK":
        raise RuntimeError(
            f"IMAP SELECT INBOX refused: {status} {_decode_imap_reason(data)!r}"
        )
    return client


async def _read_uidvalidity(client) -> int:
    status, data = await client.status("INBOX", "(UIDVALIDITY)")
    if status != "OK":
        raise RuntimeError(f"STATUS UIDVALIDITY refused: {status} / {data!r}")
    joined = b" ".join(b for b in data if isinstance(b, (bytes, bytearray)))
    m = _UIDVALIDITY_RE.search(joined)
    if not m:
        raise RuntimeError(f"could not parse UIDVALIDITY from {data!r}")
    return int(m.group(1))


async def _drain_unseen(client, uidvalidity: int) -> int:
    status, data = await client.uid_search("UNSEEN")
    if status != "OK":
        log.warning("UID SEARCH UNSEEN refused: %s / %r", status, data)
        return 0
    blob = b" ".join(b for b in data if isinstance(b, (bytes, bytearray)))
    uids = [int(tok) for tok in blob.split() if tok.isdigit()]
    if not uids:
        return 0
    log.info("draining %d UNSEEN UID(s): %s", len(uids), uids)
    processed = 0
    for uid in uids:
        if await _process_one_uid(client, uidvalidity, uid):
            processed += 1
    return processed


async def _process_one_uid(client, uidvalidity: int, uid: int) -> bool:
    """Fetch + persist + flag-as-seen one UID. Returns True if a row was
    created or already existed (i.e. we don't need the caller to retry).
    """
    if await _already_persisted_async(uidvalidity, uid):
        # Already done — just \Seen so SEARCH UNSEEN stops returning it.
        await client.uid("STORE", str(uid), "+FLAGS", r"(\Seen)")
        return True

    status, data = await client.uid(
        "FETCH", str(uid), "(BODY.PEEK[] INTERNALDATE)"
    )
    if status != "OK":
        log.warning("UID FETCH %d refused: %s / %r", uid, status, data)
        return False
    raw_eml, internaldate = _parse_fetch_response(data)
    if raw_eml is None:
        log.warning("UID FETCH %d returned no body — skipping", uid)
        return False

    try:
        row = await _parse_and_persist_async(
            raw_eml,
            uidvalidity=uidvalidity,
            uid=uid,
            internaldate=internaldate,
        )
    except Exception:
        log.exception("parse_and_persist failed for UID %d — leaving UNSEEN", uid)
        return False
    if row is not None:
        log.info(
            "inbound persisted: pk=%d uid=%d alias=%r from=%r",
            row.pk,
            uid,
            row.to_alias,
            row.from_email,
        )
    await client.uid("STORE", str(uid), "+FLAGS", r"(\Seen)")
    return True


def _is_new_mail_signal(line) -> bool:
    if not isinstance(line, (bytes, bytearray)):
        return False
    upper = bytes(line).upper()
    return b"EXISTS" in upper or b"RECENT" in upper


async def _wait_for_activity(
    client,
    *,
    idle_deadline: float,
    fallback_interval: float,
    stop_event: asyncio.Event,
) -> str:
    """Block until something interesting happens. Returns one of:
      "push"      — server pushed an EXISTS/RECENT
      "fallback"  — no push for `fallback_interval`s, do a defensive SEARCH
      "refresh"   — IDLE about to time out server-side, re-IDLE
      "stop"      — stop_event fired
    """
    while True:
        if stop_event.is_set():
            return "stop"
        remaining = idle_deadline - time.monotonic()
        if remaining <= 0:
            return "refresh"
        wait = min(remaining, fallback_interval)
        try:
            response = await asyncio.wait_for(
                client.wait_server_push(), timeout=wait
            )
        except asyncio.TimeoutError:
            return "fallback"
        if not response:
            continue
        if any(_is_new_mail_signal(line) for line in response):
            return "push"
        # Other untagged responses (FLAGS, OK heartbeat, …) — keep waiting.


async def run_session(stop_event: asyncio.Event) -> None:
    """One full session: connect → drain → IDLE-loop → graceful logout.

    Re-entered by `main()` after any failure with backoff. Returning
    normally signals "session ended cleanly, no backoff needed".
    """
    client = await _connect(
        host=settings.IMAP_HOST,
        port=settings.IMAP_PORT,
        use_ssl=settings.IMAP_USE_SSL,
        timeout=30,
    )
    try:
        uidvalidity = await _read_uidvalidity(client)
        log.info("IMAP connected: uidvalidity=%d", uidvalidity)
        await _drain_unseen(client, uidvalidity)

        idle_timeout = settings.IMAP_IDLE_TIMEOUT
        fallback_interval = settings.IMAP_FALLBACK_POLL_INTERVAL

        while not stop_event.is_set():
            idle_deadline = time.monotonic() + idle_timeout
            idle = await client.idle_start(timeout=idle_timeout)
            try:
                reason = await _wait_for_activity(
                    client,
                    idle_deadline=idle_deadline,
                    fallback_interval=fallback_interval,
                    stop_event=stop_event,
                )
            finally:
                client.idle_done()
                try:
                    await asyncio.wait_for(idle, timeout=10)
                except asyncio.TimeoutError:
                    log.warning("IDLE coroutine did not terminate within 10s")
            if reason == "stop":
                break
            if reason in ("push", "fallback"):
                await _drain_unseen(client, uidvalidity)
            # reason == "refresh" → just re-enter IDLE
    finally:
        try:
            await asyncio.wait_for(client.logout(), timeout=10)
        except Exception:
            log.debug("IMAP logout failed (already disconnected?)", exc_info=True)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _stop() -> None:
        log.info("imap_idle: signal received, shutting down")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            # Windows / restricted runtimes — Ctrl-C still raises KeyboardInterrupt.
            pass

    backoff = _BACKOFF_INITIAL
    while not stop_event.is_set():
        try:
            await run_session(stop_event)
            backoff = _BACKOFF_INITIAL
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception(
                "imap_idle: session crashed; backing off %.1fs", backoff
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, _BACKOFF_CAP)
    log.info("imap_idle: daemon stopped cleanly")
