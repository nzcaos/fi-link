"""Pure decision logic for the Phase 5b inbound pipeline.

Everything here is side-effect-light and unit-testable in isolation:
suppression heuristics, sender resolution, the send-permission resolver, the
top-level decision algorithm, and DSN/body parsing. The orchestration that
ties these together and performs the DB writes / task deferrals lives in
``lists.tasks.process_inbound``.

The split mirrors PLAN.md's call-out that the decision algorithm is one of the
critical paths that must carry unit tests.
"""
from __future__ import annotations

import email
import email.message
import email.policy
import logging
import re
from dataclasses import dataclass
from enum import Enum

from .models import List, ListAccess, ListSendPermission

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MIME parsing helpers
# ---------------------------------------------------------------------------


def parse_message(raw_eml: bytes) -> email.message.EmailMessage:
    """Parse raw RFC-822 bytes with the forgiving modern policy. Never raises:
    a truly broken blob yields an empty message so the caller can still take a
    decision (typically SUPPRESSED/UNKNOWN) instead of crashing the worker.
    """
    try:
        return email.message_from_bytes(raw_eml, policy=email.policy.default)
    except Exception as exc:  # noqa: BLE001 — defensive, parser tree is wide
        log.warning("parse_message: bad MIME (%s) — using empty message", exc)
        return email.message.EmailMessage()


def extract_subject_and_body(msg: email.message.EmailMessage) -> tuple[str, str]:
    """Pull the Subject and a text body out of a parsed message.

    Prefers ``text/plain``; falls back to ``text/html`` (raw markup — we do not
    attempt to strip tags in v1, the recipient's MUA renders it). Returns
    ``("", "")``-ish defaults rather than raising on odd structures.
    """
    subject = (msg.get("Subject") or "").strip()
    body = ""
    try:
        part = msg.get_body(preferencelist=("plain",))
        if part is None:
            part = msg.get_body(preferencelist=("html",))
        if part is not None:
            body = part.get_content()
    except Exception as exc:  # noqa: BLE001
        log.warning("extract_subject_and_body: %s", exc)
        body = ""
    return subject, body if isinstance(body, str) else str(body)


_MSGID_RE = re.compile(r"<([^<>]+)>")


def referenced_message_ids(msg: email.message.Message) -> set[str]:
    """Message-IDs this mail references via In-Reply-To / References, stripped
    of angle brackets. Used to detect replies to mail we forwarded out.
    """
    out: set[str] = set()
    for header in ("In-Reply-To", "References"):
        value = msg.get(header)
        if not value:
            continue
        for match in _MSGID_RE.findall(value):
            cleaned = match.strip()
            if cleaned:
                out.add(cleaned)
    return out


# ---------------------------------------------------------------------------
# Suppression (anti-loop / autoresponder)
# ---------------------------------------------------------------------------


def suppression_reason(
    msg: email.message.Message, *, check_in_reply_to: bool
) -> str | None:
    """Return a short reason string if the message must be SUPPRESSED (not
    forwarded), else None. Combines the signals from CLAUDE.md /
    "Autoresponder / loop suppression" — none is reliable alone:

    * ``Auto-Submitted`` header present and not ``no`` (RFC 3834).
    * Empty ``Return-Path: <>`` (bounce convention; also DSNs).
    * ``Precedence: bulk | list | junk`` (legacy, still widely set).
    * Only when ``check_in_reply_to``: ``In-Reply-To`` / ``References``
      matching a Message-ID we sent — the strongest OOO signal. This is
      checked **only for list-addressed mail**; for replies to anonymisation
      aliases the In-Reply-To always matches by construction, so applying it
      there would suppress every legitimate reply.
    """
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return f"Auto-Submitted: {auto}"

    return_path = (msg.get("Return-Path") or "").strip()
    if return_path == "<>":  # empty envelope-from = bounce/auto
        return "leerer Return-Path (Bounce/Auto)"

    precedence = (msg.get("Precedence") or "").strip().lower()
    if precedence in ("bulk", "list", "junk"):
        return f"Precedence: {precedence}"

    if check_in_reply_to:
        refs = referenced_message_ids(msg)
        if refs:
            # Imported lazily to keep this module import-cheap and avoid a
            # cycle with tasks at module-load time.
            from .models import OutboundMessage

            if OutboundMessage.objects.filter(message_id__in=refs).exists():
                return "Antwort auf eine von uns weitergeleitete Nachricht"
    return None


# ---------------------------------------------------------------------------
# Sender identification + send-permission resolution
# ---------------------------------------------------------------------------


def identify_sender_users(from_email: str):
    """Resolve a From-address to the set of active USERs whose PERSON carries
    that email. The set may have more than one element (shared family mailbox,
    CLAUDE.md / "PERSON vs. USER"); any one of them being a member / permitted
    sender is sufficient.
    """
    from accounts.models import User

    if not from_email:
        return User.objects.none()
    return User.objects.filter(
        person__email__iexact=from_email.strip(), is_active=True
    )


def sender_is_member(target_list: List, sender_users) -> bool:
    """True if any of the sender's USERs is in the target list's Benutzergruppe."""
    if not sender_users:
        return False
    return ListAccess.objects.filter(
        list=target_list, user__in=sender_users
    ).exists()


def _ancestor_ids(target_list: List) -> list[int]:
    """Walk the parent chain upward (excluding the list itself). Guards against
    a malformed cycle so a bad parent edge can't spin the worker.
    """
    ids: list[int] = []
    seen: set[int] = {target_list.pk}
    parent_id = target_list.parent_id
    while parent_id and parent_id not in seen:
        ids.append(parent_id)
        seen.add(parent_id)
        parent_id = (
            List.objects.filter(pk=parent_id)
            .values_list("parent_id", flat=True)
            .first()
        )
    return ids


def resolve_send_permission(
    target_list: List, member_list_ids: set[int]
) -> tuple[bool, bool]:
    """Resolve whether a sender who is a member of ``member_list_ids`` may send
    to ``target_list``, and whether a release click is required.

    Returns ``(permitted, requires_release_click)``. Combines, per CLAUDE.md /
    "List send permissions":

    1. Explicit ``LIST_SEND_PERMISSION`` rows on the target.
    2. The implicit parent grant: a member of the target's **direct parent**
       may send without a release click — unless an explicit row for
       ``(target, parent)`` exists, in which case that explicit row governs
       (so an admin can tighten the implicit default).
    3. Transitive grants (``transitive=True``) on any **ancestor** of the
       target.

    ``requires_release_click`` is the most-permissive resolution: if any
    matching grant is click-free, no click is required.
    """
    if not member_list_ids:
        return (False, False)

    click_values: list[bool] = []

    explicit = ListSendPermission.objects.filter(
        target_list=target_list, granted_to_list_id__in=member_list_ids
    )
    explicit_grantor_ids: set[int] = set()
    for sp in explicit:
        click_values.append(sp.requires_release_click)
        explicit_grantor_ids.add(sp.granted_to_list_id)

    # Implicit parent grant — only when not overridden by an explicit row.
    if (
        target_list.parent_id
        and target_list.parent_id in member_list_ids
        and target_list.parent_id not in explicit_grantor_ids
    ):
        click_values.append(False)

    # Transitive grants from ancestors.
    ancestor_ids = _ancestor_ids(target_list)
    if ancestor_ids:
        for sp in ListSendPermission.objects.filter(
            target_list_id__in=ancestor_ids,
            transitive=True,
            granted_to_list_id__in=member_list_ids,
        ):
            click_values.append(sp.requires_release_click)

    if not click_values:
        return (False, False)
    return (True, all(click_values))


# ---------------------------------------------------------------------------
# Top-level decision algorithm
# ---------------------------------------------------------------------------


class Outcome(str, Enum):
    """What to do with a list-addressed message once suppression has passed."""

    MEMBER_RELEASE = "member_release"  # sender is a member → release link to sender
    FORWARD = "forward"  # permitted sender, no click → forward immediately
    PERMITTED_RELEASE = "permitted_release"  # permitted sender, click required
    ADMIN_APPROVAL = "admin_approval"  # neither → list admins decide


@dataclass(frozen=True)
class Decision:
    outcome: Outcome


def decide(target_list: List, sender_users) -> Decision:
    """The inbound decision algorithm (CLAUDE.md / "Inbound mail decision
    algorithm"):

    1. Identify sender USER(s) via From (done by the caller, passed in).
    2. Sender is a member of the target → release link to the sender
       (anti-spoofing — always a click, never a direct forward).
    3. Otherwise, if the sender holds send permission (direct / implicit
       parent / transitive) → accept, with or without a release click per the
       grant.
    4. Otherwise → admin-approval.
    """
    sender_users = list(sender_users)
    if not sender_users:
        return Decision(Outcome.ADMIN_APPROVAL)
    if sender_is_member(target_list, sender_users):
        return Decision(Outcome.MEMBER_RELEASE)

    member_list_ids = set(
        ListAccess.objects.filter(user__in=sender_users).values_list(
            "list_id", flat=True
        )
    )
    permitted, requires_click = resolve_send_permission(target_list, member_list_ids)
    if permitted:
        return Decision(
            Outcome.PERMITTED_RELEASE if requires_click else Outcome.FORWARD
        )
    return Decision(Outcome.ADMIN_APPROVAL)


# ---------------------------------------------------------------------------
# DSN (bounce) parsing
# ---------------------------------------------------------------------------


def looks_like_dsn(msg: email.message.Message) -> bool:
    """Heuristic: a delivery-status notification is ``multipart/report`` with
    ``report-type=delivery-status``, or simply contains a
    ``message/delivery-status`` part. We don't *rely* on this — bounce
    correlation keys on the ``bounce-<token>@`` envelope alias — but it lets
    us enrich the stored reason.
    """
    ctype = (msg.get_content_type() or "").lower()
    if ctype == "multipart/report":
        return True
    try:
        for part in msg.walk():
            if part.get_content_type() == "message/delivery-status":
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _delivery_status_text(part: email.message.Message) -> str:
    """Render a ``message/delivery-status`` part to text. The stdlib may expose
    its payload either as a raw string or as a list of sub-Messages (the
    per-MTA / per-recipient field blocks) depending on version/policy; handle
    both so the field regex below works either way.
    """
    payload = part.get_payload()
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        chunks = []
        for sub in payload:
            try:
                chunks.append(sub.as_string())
            except Exception:  # noqa: BLE001
                chunks.append(str(sub))
        return "\n".join(chunks)
    try:
        return part.as_string()
    except Exception:  # noqa: BLE001
        return ""


def parse_dsn(msg: email.message.Message) -> tuple[str, str]:
    """Best-effort extraction of ``(status, diagnostic)`` from a DSN's
    ``message/delivery-status`` part. Returns empty strings when the structure
    isn't parseable — correlation does not depend on this (it keys on the
    ``bounce-<token>@`` envelope alias), this only enriches the stored reason.
    """
    status = ""
    diagnostic = ""
    action = ""
    try:
        for part in msg.walk():
            if part.get_content_type() != "message/delivery-status":
                continue
            text = _delivery_status_text(part)
            m = re.search(r"^Status:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
            if m:
                status = m.group(1).strip()
            m = re.search(
                r"^Diagnostic-Code:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE
            )
            if m:
                diagnostic = m.group(1).strip()
            m = re.search(r"^Action:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
            if m:
                action = m.group(1).strip()
            break
    except Exception as exc:  # noqa: BLE001
        log.debug("parse_dsn: %s", exc)
    if not status and action:
        status = f"action={action}"
    return status, diagnostic
