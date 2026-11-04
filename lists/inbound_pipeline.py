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
    """Pull the Subject and the **text** body out of a parsed message.

    Prefers ``text/plain``. An HTML-only mail is reduced to its text
    (`html_to_text`) — this body goes out as the ``text/plain`` part of the
    forward, and raw markup there is what recipients saw as
    ``<html><body><div style=…`` (2026-11). The HTML itself is forwarded
    unchanged as the ``text/html`` alternative, read from the stored original
    at send time (`forward_html_body`). Returns ``("", "")``-ish defaults rather
    than raising on odd structures.
    """
    subject = (msg.get("Subject") or "").strip()
    body = ""
    try:
        part = msg.get_body(preferencelist=("plain",))
        if part is not None:
            body = part.get_content()
        else:
            part = msg.get_body(preferencelist=("html",))
            if part is not None:
                body = html_to_text(part.get_content())
    except Exception as exc:  # noqa: BLE001
        log.warning("extract_subject_and_body: %s", exc)
        body = ""
    return subject, body if isinstance(body, str) else str(body)


def html_to_text(html: str) -> str:
    """Readable text of an HTML body: line breaks at block ends, tags dropped,
    entities resolved, link targets kept as ``Text (URL)``. Only for the text
    alternative and previews — the HTML part is forwarded as sent."""
    from html import unescape

    from django.utils.html import strip_tags

    html = str(html or "")
    html = re.sub(r"(?is)<(script|style|head|title)\b.*?</\1\s*>", "", html)

    def _link(m: re.Match) -> str:
        href, label = m.group(2).strip(), m.group(3)
        shown = unescape(strip_tags(label)).strip()
        target = unescape(href)
        if not target or target.lower().startswith(("mailto:", "#", "javascript:")) or target in shown:
            return label
        return f"{label} ({href})"

    html = re.sub(r'(?is)<a\b[^>]*?\bhref\s*=\s*(["\'])(.*?)\1[^>]*>(.*?)</a\s*>', _link, html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h[1-6]>|</blockquote>", "\n", html)
    text = unescape(strip_tags(html)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text).strip()


def _contains(container, part) -> bool:
    """Whether ``part`` lies within ``container``'s MIME subtree (not
    descending into attached ``message/rfc822`` mails)."""
    if container is part:
        return True
    if container.get_content_type() == "message/rfc822" or not container.is_multipart():
        return False
    return any(_contains(sub, part) for sub in container.iter_parts())


def forward_html_body(msg: email.message.EmailMessage) -> str | None:
    """The sender's HTML body, to forward unchanged as the ``text/html``
    alternative — formatting, links and embedded images arrive as written.
    The HTML is not sanitised: it reaches the list only after the sender's
    release click or an admin's approval.

    ``None`` when there is no HTML body, or when it is not an alternative of
    the text body (e.g. ``multipart/mixed`` carrying a separate inline HTML
    part next to the text) — forwarding that as "the same mail in HTML" would
    show recipients different content than the text part.
    """
    try:
        html_part = msg.get_body(preferencelist=("html",))
        if html_part is None:
            return None
        plain_part = msg.get_body(preferencelist=("plain",))
        if plain_part is not None and not any(
            p.get_content_type() == "multipart/alternative"
            and _contains(p, plain_part)
            and _contains(p, html_part)
            for p in _mime_parts(msg)
        ):
            return None
        html = html_part.get_content()
    except Exception as exc:  # noqa: BLE001
        log.warning("forward_html_body: %s", exc)
        return None
    if not isinstance(html, str) or not html.strip():
        return None
    # The part is re-encoded as UTF-8; a stale <meta charset> would make some
    # clients decode it with the original charset.
    return re.sub(r"(?i)(<meta\b[^>]*?charset\s*=\s*[\"']?)[\w.:-]+", r"\g<1>utf-8", html)


def _mime_parts(msg):
    """All MIME parts, depth-first, without descending into attached mails."""
    yield msg
    if msg.get_content_type() != "message/rfc822" and msg.is_multipart():
        for sub in msg.iter_parts():
            yield from _mime_parts(sub)


_CID_REF_RE = re.compile(r"(?i)\bcid:([^\s\"'<>)]+)")


def html_inline_images(
    msg: email.message.EmailMessage, html: str | None
) -> list[tuple[str, str, bytes, str]]:
    """Images the HTML body shows via ``cid:`` references, as
    ``(content_id, filename, data, content_type)`` — they go into the
    forward's ``multipart/related`` next to the HTML. Looked up anywhere in the
    mail (some clients put them in ``multipart/mixed``), so they are not
    forwarded a second time as attachments (`extract_attachments`)."""
    from urllib.parse import unquote

    if not html:
        return []
    refs = {unquote(r).lower() for r in _CID_REF_RE.findall(html)}
    if not refs:
        return []
    out: list[tuple[str, str, bytes, str]] = []
    seen: set[str] = set()
    try:
        for part in _mime_parts(msg):
            if part.is_multipart() or part.get_content_maintype() != "image":
                continue
            cid = str(part.get("Content-ID") or "").strip().strip("<>").strip()
            if not cid or cid.lower() not in refs or cid.lower() in seen:
                continue
            data = part.get_payload(decode=True)
            if not data:
                continue
            seen.add(cid.lower())
            out.append((cid, clean_filename(part.get_filename()), data, part.get_content_type()))
    except Exception as exc:  # noqa: BLE001
        log.warning("html_inline_images: %s", exc)
    return out


def clean_filename(name: str | None) -> str:
    """A MIME filename safe to put back into a header.

    RFC 2231 names are percent-decoded, so CR/LF and other controls can
    arrive here; they would break (or inject into) the forward's
    Content-Disposition header. Unicode spaces (NBSP, the narrow NBSP in
    macOS screenshot names) are not "printable" but harmless: keep them as
    plain spaces.
    """
    import unicodedata

    return "".join(
        " " if unicodedata.category(ch) == "Zs" else ch
        for ch in (name or "")
        if ch.isprintable() or unicodedata.category(ch) == "Zs"
    ).strip()


def message_preview(msg: email.message.EmailMessage) -> tuple[str, list[str]]:
    """Readable text of a held mail plus its attachment names, for the people
    deciding on a release (admin approval). An HTML-only mail is reduced to
    its text — the preview is rendered escaped, never as markup. Attachments
    are listed so the approver knows which files go along.
    """
    text = ""
    try:
        part = msg.get_body(preferencelist=("plain",))
        if part is not None:
            text = part.get_content()
        else:
            part = msg.get_body(preferencelist=("html",))
            if part is not None:
                text = html_to_text(part.get_content())
    except Exception as exc:  # noqa: BLE001
        log.warning("message_preview: %s", exc)
    text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", str(text or "")).strip()

    attachments = [name for name, _data, _ctype in extract_attachments(msg)]
    return text, attachments


def extract_attachments(msg: email.message.EmailMessage) -> list[tuple[str, bytes, str]]:
    """The attachments of a mail as ``(filename, data, content_type)``, in
    order — what a forward carries alongside the text and HTML body.

    Everything that is not the chosen text/HTML body counts, except images
    embedded in the HTML body: those referenced via ``cid:`` travel inside the
    forward's ``multipart/related`` (`html_inline_images`), and unreferenced
    ``multipart/related`` parts with a Content-ID are not files of their own.
    Inline images Apple Mail puts in ``multipart/mixed`` without an HTML
    reference *are* kept: there they are the content. A forwarded mail
    (``message/rfc822``) becomes an ``.eml`` attachment.
    """
    import mimetypes

    body_ids = set()
    for pref in (("plain",), ("html",)):
        try:
            part = msg.get_body(preferencelist=pref)
        except Exception:  # noqa: BLE001
            part = None
        if part is not None:
            body_ids.add(id(part))
    inline_cids = {
        cid.lower() for cid, _n, _d, _c in html_inline_images(msg, forward_html_body(msg))
    }

    out: list[tuple[str, bytes, str]] = []

    def _name(part, fallback_ext: str = "") -> str:
        name = clean_filename(part.get_filename())
        if name:
            return name
        ext = mimetypes.guess_extension(part.get_content_type()) or fallback_ext
        return f"Anhang-{len(out) + 1}{ext}"

    def _visit(part, in_related: bool) -> None:
        ctype = part.get_content_type()
        if ctype == "message/rfc822":
            inner = part.get_payload()
            inner = inner[0] if isinstance(inner, list) and inner else inner
            if hasattr(inner, "as_bytes"):
                out.append((_name(part, ".eml"), inner.as_bytes(), ctype))
            return
        if part.is_multipart():
            related = ctype == "multipart/related"
            for sub in part.iter_parts():
                _visit(sub, related)
            return
        if id(part) in body_ids:
            return
        disposition = part.get_content_disposition()
        cid = str(part.get("Content-ID") or "").strip().strip("<>").strip().lower()
        if cid and part.get_content_maintype() == "image" and cid in inline_cids:
            return  # shown in the HTML body, forwarded inline with it
        if in_related and cid:
            return  # part of the HTML body, not a file of its own
        if part.get_content_maintype() == "text" and not part.get_filename() and disposition != "attachment":
            return  # an alternative body part, not a file
        data = part.get_payload(decode=True)
        if data is None:
            return
        out.append((_name(part), data, ctype))

    try:
        _visit(msg, False)
    except Exception as exc:  # noqa: BLE001
        log.warning("extract_attachments: %s", exc)
    return out


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
