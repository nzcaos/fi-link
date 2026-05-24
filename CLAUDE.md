# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

The repository is initialized on branch `main` and pushed to GitHub (`origin = https://github.com/nzcaos/fi-link.git`). It currently contains the specification (`filink.md`, in German), the architecture decisions captured in this file, and a `.gitignore` for the planned Python/Django stack. There is no application code, build system, dependency manifest, or test suite yet — but the architecture is decided end-to-end (mail layer, application stack, task queue, IMAP IDLE consumer, domain model, authentication) and implementation is the next concrete step.

## What "Fichtelink" is

A self-service mailing-list and contact-list platform, originally motivated by school parent groups (e.g. "Eltern der Klasse 9c"). Users register themselves, create or join lists, and the system both stores structured per-user data and forwards email sent to a list's address to its members. The spec is in German; UI/data terms in code should generally follow the spec's German vocabulary (`Liste`, `Vorlage`, `Eintrag`, `Benutzergruppe`, …) unless a translation decision is made later.

## Core domain model (from `filink.md`)

The data model in the spec uses these entities — keep these names and relationships in mind, as much of the access-control logic depends on them:

- **USER** — registers themselves, has email.
- **LIST** — a list with a title, an email address, and a `LISTTEMPLATE`. Created by a user, who becomes a `LIST_ADMIN`. Lists are public-visible, public-editable, or private. Private lists are joined via an invitation link (shareable as URL / QR code).
- **LIST_ADMIN** / **LIST_ACCESS** — admins of a list; users who have accepted invitations (the "Benutzergruppe" of a list).
- **LISTTEMPLATE** + **LIST_ATTRIBUT** + **LIST_ATTRIBUT_VALUE** — templates define fields (text, email, phone, number, choice with optional usage cap, checkbox, user-relationship). Templates are super-admin only. `Must_be_public` on an attribute means a user cannot hide that field.
- **LIST_RECORD** + **LIST_RECORD_VALUE** — one record per user per list (each user can have at most one entry per list). A record is owned by its creator; only the owner edits it.
- **LIST_RECORD_ACCESS** — per-value visibility, scoped by audience (another `List_ID`, with `0` meaning public). Each owner controls visibility per field per user group.
- **FORM** / **FORM_PART** / **FORM_PART_ASSET** / **FORM_ACCESS** — composable forms (static HTML parts + a dynamic list part) with assets (images) and per-user access.

### Important access-control / hierarchy rules

- Visibility of a list-record field is per-audience. The audience is a `List_ID`, so "show this field to members of list X" is the primitive.
- Lists form an implicit hierarchy via admin overlap: if an admin of list A is a member of list B, then B is a possible **übergeordnete** (parent) list of A. The spec uses this to e.g. let the Elternbeirat send mail to a class list.
- Every user is automatically a member of every public list's Benutzergruppe.

## Mailing-list behavior (must be implemented carefully)

- The server pulls mail via **IMAP IDLE** on a single **catch-all** address; per-list addresses are virtual (no real mailboxes).
- Incoming mail to a list address is **not stored** — only the reception and forwarding are logged.
- **Sender-is-member path:** before forwarding, the server sends the sender a confirmation email with a release link. Mail is only forwarded after that link is clicked. This is the anti-spoofing mechanism — do not skip it.
- **Sender-is-not-member path:** the release link goes to a list admin, who decides whether to forward.
- **Anonymization:** if the sender is a member but has not consented to publishing their address, the From: address is rewritten to an auto-generated alias on the list-server domain before forwarding. Replies to that alias must be routed back to the original sender by the server.
- A list admin can configure who is permitted to send to the list — including members of a parent (übergeordnete) list, per the hierarchy rule above.

## Architecture decisions (domain model)

These were agreed with the project owner on 2026-05-24 and in places extend or modify the spec's model. Where the *Core domain model* section above describes the spec, this section describes how it will actually be implemented.

### PERSON vs. USER

The spec entity USER conflates two concepts: a person represented in lists, and an account that can log in. These are separated:

- **PERSON** is the entity that appears as the subject of a `LIST_RECORD`. Holds names and other attribute values.
- **USER** is a PERSON with login credentials. Relation is 1:0..1 — every USER has exactly one PERSON; a PERSON may or may not have a USER.

Consequences:

- `LIST_RECORD.subject` references PERSON, not USER. A child in a class list is a PERSON without a USER.
- `LIST_ACCESS` (the Benutzergruppe — those who can *see* the list) remains USER-based. PERSONs without USERs are never audience members and see nothing.
- `LIST_ADMIN` remains USER-based for the same reason.
- Audience-based visibility (`LIST_RECORD_ACCESS.audience = list_id`) is evaluated at runtime as "is the requesting USER in the Benutzergruppe of audience list?" — semantics unchanged.

**Email belongs to PERSON, not USER.** `PERSON.email` is nullable (e.g. children can be modeled without an email). USERs inherit their addressable email through their PERSON. A consequence: **multiple USERs may share the same email address** — most commonly when both parents in a family use a shared mailbox, each registering as a separate USER under separate PERSONs that happen to share `PERSON.email`. The natural uniqueness for USER is `(person_id)`, not `(email)`. Follow-on implications: login disambiguation happens via the WebAuthn credential picker (see *Architecture decisions (authentication)*), and inbound-mail handling resolves a sender's From-address to a *set* of USERs — any of them clicking a release link satisfies the anti-spoofing check, since the check is "did someone with access to this mailbox confirm?".

### Family-association model

Some lists (notably school class lists) have *members* who are not USERs (children) and *associated persons* who are USERs (parents/guardians). To express this without ad-hoc workarounds:

- **`LIST_RECORD.role`** distinguishes `member` (the subject is a real member of the list, e.g. the child) from `associate` (the subject is associated with a member but not themselves a member, e.g. the parent). Defaults are set by the LISTTEMPLATE.
- **`PERSON_RELATIONSHIP`** captures the connection: `subject_person_id` (the member, e.g. the child), `related_person_id` (the associate, e.g. the parent), `role` (a string from a taxonomy: "Mutter von", "Vater von", "Erziehungsberechtigte von", "Großmutter von", …). The role taxonomy is **configurable per LISTTEMPLATE**, not hardcoded — different list types use different vocabularies (school parent group vs. adult-education course vs. association membership).
- **`RECORD_MANAGER`** captures who has edit rights to a given record: `(record_id, user_id, since, basis)` with `basis ∈ {creator, invited, guardian, self_registered}`. This replaces the spec's "only the creator edits": a child's record is editable by all USERs registered as their guardians; a parent's record is editable by themselves once they self-register, plus by the original creator (co-edit, with a "last edited by …" hint in the UI).

Siblings in different classes fall out naturally: the parent's USER has one `LIST_RECORD` (role=associate) per class plus one `PERSON_RELATIONSHIP` per child. No special casing.

### Onboarding modes per LISTTEMPLATE

The LISTTEMPLATE has a field **`member_subject_mode`** with two values, which the onboarding wizard branches on:

- `self` — the person doing the registration *is* the member. Creates one PERSON + one USER + one LIST_RECORD (role=member). Examples: VHS-Kursliste, Förderverein-Mitgliederverzeichnis.
- `via_associate` — the registering USER is not themselves the member; they declare the actual member (a PERSON, e.g. a child) and become their associate. Examples: Schulklassenliste.

QR-code and invitation flows are otherwise identical — only the entry point differs:

- **QR-code**: no prefilled data, the wizard starts blank.
- **Invitation**: name and email prefilled from the inviter's input; on registration the stub USER (created earlier by the inviter) is activated and `RECORD_MANAGER` is updated with `basis=invited`.

### List hierarchy

Lists form an explicit tree via **`LIST.parent_list_id`** (nullable, 1:N). When creating a sub-list the creator selects the parent from lists in which they have admin or member status. The spec's "implicit hierarchy from admin overlap" is **not used** — explicit configuration is clearer, versioned, and survives role changes (e.g. an Elternvertreter resigning does not break the Klasse → Elternbeirat link).

N:M parenthood (a list with multiple parents, e.g. a class as child of both Elternbeirat and Förderverein) is deliberately not supported in v1. Migration to N:M is later a non-breaking change.

Example hierarchy for a school deployment:

```
Elternbeirat
├── Klasse 5a
│   ├── Klassenfeier-Planung
│   └── Umfrage: Pizza-Wahl
├── Klasse 5b
└── …
Lehrerkollegium   (separate top-level list, not in the Eltern tree)
```

### List send permissions

Beyond the spec's two cases (member-sends-with-release-link, non-member-with-admin-approval), one list's members can be granted direct send rights to another list's address. Modeled as **`LIST_SEND_PERMISSION`**:

| Column | Meaning |
|---|---|
| `target_list_id` | The list being sent to |
| `granted_to_list_id` | The list whose members get send rights |
| `granted_at`, `granted_by_user_id` | Audit |
| `requires_release_click` | If `false`, no confirmation step needed |
| `transitive` | If `true`, the permission propagates to sub-lists of the target. Default `false`. |

**Implicit default from hierarchy:** if `list.parent_list_id = X`, an implicit `SEND_PERMISSION (target=list, granted_to=X, requires_release_click=false)` exists. The admin of the sub-list may disable this. This implements the spec's "members of a parent list may send" automatically.

**Explicit cross-list grants** (e.g. Lehrerkollegium → Klassenliste) are added by the target list's admin, default `requires_release_click=false` — otherwise the permission would be functionally equivalent to the normal approval flow.

**Transitive inheritance** is rarely needed in practice: sub-list memberships are typically subsets of parent-list memberships, so a parent-list member already reaches sub-list recipients via the regular parent path. The column exists but is `false` by default and gets no UI in v1.

**Inbound mail decision algorithm:**

1. Identify sender USER via From.
2. If sender is a member of the target list → normal release-link flow (anti-spoofing).
3. Otherwise check `LIST_SEND_PERMISSION` (direct or, if applicable, via `transitive=true` from a parent target): if sender is a member of any list with permission on the target, accept (with or without release-click per the permission row).
4. Otherwise → admin-approval flow.

### List display defaults via LISTTEMPLATE

Which fields are shown when a list is opened, and how they're grouped per row, is part of the LISTTEMPLATE — not hardcoded UI. For a school-class template, the default view should reproduce the information density of the paper class list it replaces: child's name, address, mother's name + phone + email, father's name + phone + email, all visible per row. For other LISTTEMPLATEs the default view is correspondingly different.

### Super-Admin scope

Beyond the spec's "Super-Admin manages LISTTEMPLATEs", the Super-Admin also manages **top-level lists** — those without a `parent_list_id`, e.g. `Elternbeirat`, `Lehrerkollegium`. Regular USERs cannot create top-level lists themselves: setting up the structural top of an installation is an installation-administrative act, not a self-service action.

### Encryption at rest

`LIST_RECORD_VALUE.value` is encrypted at the application layer with Fernet (AES-128-CBC + HMAC), using a **single installation-wide key** from an environment variable. **All values are encrypted unconditionally** — no per-field opt-in/out, to eliminate the risk of forgetting. Library: `django-cryptography` or equivalent.

Trade-off: SQL `WHERE` / `ORDER BY` / `LIKE` on encrypted columns is no longer available. Acceptable for Fichtelink — the UI shows lists row-by-row, and full-text search across PII was never a feature. Key rotation is supported via the library's multi-key mechanism (old + new active simultaneously, re-encrypt over time).

`PERSON.email` remains plaintext — it is the addressable identifier used for activation links, SMTP routing of forwarded mail, In-Reply-To correlation, bounce addressing, and as From/To in mail headers we send out. Disk-level encryption (e.g. LUKS on the VPS) is orthogonal and a deployment concern.

## Architecture decisions (authentication)

Agreed with the project owner on 2026-05-24.

### Passkeys only, no passwords

Authentication is **exclusively** via WebAuthn passkeys. No password storage, no password-reset flow, no password-based login backend. Rationale: passwords can be forgotten, chosen poorly, reused across services, and leaked in third-party breaches — none of these failure modes apply to passkeys. The library is `django-allauth` (≥ 0.54), configured with the password backend disabled and WebAuthn enabled.

Trade-off: users without passkey-capable devices (older Windows, no smartphone) cannot use the system at all. This is accepted deliberately — the volunteer-run target audience is overwhelmingly on devices that support passkeys (iOS 16+, Android 9+, modern macOS / Windows / Linux with current browsers), and the security gain is judged to outweigh the exclusion. Setup documentation must call this requirement out clearly.

### Registration and login flow

- **Registration:** user enters email → confirmation link sent to that address → on click, the browser prompts for passkey enrollment (Touch ID, Face ID, Windows Hello, or a hardware security key) → account is active.
- **Invitation (family-triade activation):** the invitation link *is* the confirmation link — clicking it activates the stub USER previously created by the inviter and triggers passkey enrollment in a single step.
- **Login:** preferred path is the *usernameless* / discoverable-credentials flow — the user clicks "Sign in", the browser presents available passkeys (labeled by WebAuthn `user.displayName`, e.g. "Anna Müller — Fichtelink"), the user picks one, the corresponding USER is signed in. No email entry needed.
- **Multiple passkeys per account** are encouraged: the UI lets users enroll passkeys on additional devices (e.g. "iPhone", "Arbeit-Laptop") and delete individual ones. Multi-device enrollment is the primary defense against device loss and reduces the recovery burden.

The usernameless flow combined with `user.displayName` cleanly handles the shared-family-email case (see *PERSON vs. USER*): both parents register passkeys under the same `PERSON.email`, and the browser shows both passkeys labeled by name when either parent signs in — even on a shared device.

### Account recovery

There is **no automated email-based recovery** (no "forgot password" mail link). Recovery is **human-mediated** and walks up the list-admin hierarchy:

```
Regular USER             → contacts their list admin (e.g. Elternvertreter)
List admin (sub-list)    → contacts the parent-list admin (e.g. Vorsitz Elternbeirat)
Top-level list admin     → contacts the Super-Admin
Super-Admin              → out-of-band recovery via shell
                           (e.g. `manage.py reset-passkeys --user <email>`)
```

The admin one level above triggers (manually, after recognizing the requester as the legitimate person) a resend of the activation link to the on-file email. Because the requester's data, list memberships, and family relationships are all retained, re-onboarding reduces to enrolling a new passkey — nothing else.

This model deliberately removes the standard "compromised mailbox = account takeover" recovery risk: a stolen email account alone does not yield system access; an attacker would additionally need to socially engineer a human admin who knows the legitimate user. Trade-off: recovery is asynchronous and requires admin availability — accepted given the volunteer/social context this system runs in.

### HTTPS requirement

WebAuthn works only over HTTPS (or `localhost` for development). TLS is therefore a hard deployment requirement, not optional. Recommended path for the volunteer-run target audience: Let's Encrypt with automatic renewal via the hosting provider's standard tooling. Setup documentation must call this out — a deployment without HTTPS cannot log anyone in.

## Architecture decisions (mail layer)

These were agreed with the project owner on 2026-05-24 and are independent of the application-stack decisions below.

### Mail transport

- **External mail provider, not a self-hosted MTA.** Rationale: spam-filter quality scales with provider size; running DNS/SPF/DKIM/DMARC, IP-reputation, and blacklist monitoring is inappropriate for the volunteer-run target audience. This deliberately reverses an earlier "run Postfix + LMTP" proposal.
- **Receive via IMAP IDLE on a single catch-all mailbox** (spec-conform). Per-list addresses remain virtual, resolved by the app.
- **Send via the same provider's SMTP submission**, so outbound IP reputation is the provider's, not ours.
- **Provider-agnostic.** Target deployment is volunteer-run school parent associations / Fördervereine on the smallest paid tier (~1 mailbox, ~5 GB). Hard requirements on any supported provider: catch-all + IMAP IDLE + SMTP submission. Concrete fits today: Mailbox.org Standard, Migadu Micro. Gmail / Google Workspace does **not** fit (no catch-all on cheap tiers) — call this out in user-facing setup docs so volunteers don't pick a tariff that can't host the install.

### Required state store

Anti-loop detection, bounce correlation, reply-routing, and double-receive suppression all depend on persisting message metadata. In PostgreSQL, two tables:

- **Outbound**: `Message-ID`, original-sender, recipient (list member), alias-token (for anonymization or bounce routing), sent-at, `list_id`.
- **Inbound**: `Message-ID`, From, To-alias, received-at, decision (`forwarded` / `pending-approval` / `rejected` / `suppressed`), reason, optional link to the matching Outbound row.

This one structure carries: bounce correlation, rebounce detection, double-receive suppression, OOO/autoresponder suppression, and reply-routing back to the original sender.

### Autoresponder / loop suppression

A message addressed to a list must be **suppressed** (not forwarded) if **any** of the following is true. None of these signals alone is reliable; combine all of them:

- `Auto-Submitted` header is present with any value other than `no` (RFC 3834).
- Empty `Return-Path: <>` (bounce convention — also handles DSNs).
- `Precedence: bulk` / `list` / `junk` (legacy, still widely set).
- `In-Reply-To` / `References` matches a `Message-ID` in the Outbound table — i.e. the inbound is a reply to a mail we forwarded out. In practice this is the strongest OOO signal.

Outgoing list mail must carry `List-Id`, `List-Post`, and `List-Unsubscribe` headers so RFC-conformant autoresponders skip it before any of the above triggers are needed.

### Bounce handling

Delivery Status Notifications (RFC 3464) arrive as ordinary inbound mail to the catch-all. The inbound pipeline parses DSNs and correlates `failed-recipient` against the Outbound table. No provider-specific webhook or API is used or needed — a deliberate consequence of provider-agnosticism.

### Envelope-From / SRS

Envelope-From on outbound mail to list members must **never** be the original sender — relaying via our provider IP would break the original sender's SPF and the mail would land in spam. Use the same alias mechanism as anonymization (`alias-<token>@<domain>`); for non-anonymized forwards, use a technical bounce-only alias (`bounce-<token>@<domain>`). This guarantees every DSN comes back to a token we can correlate to the original outbound row.

### Retention

- **IMAP server:** `EXPUNGE` processed messages after a 7-day grace period.
- **Local metadata (Inbound/Outbound rows):** keep 30–90 days for bounce correlation.

With this policy the smallest 5 GB provider tier holds long-term.

## Architecture decisions (application stack)

Agreed with the project owner on 2026-05-24.

- **Django** as the web framework. Rationale: Python is widely known in the volunteer maintainer pool, which matters for long-term open-source maintainability; Django Admin is a free MVP for super-admin work on `LISTTEMPLATE`; `django-allauth` covers self-registration and passkey-only authentication (see *Architecture decisions (authentication)*); `django-guardian` maps onto the per-object visibility model in `LIST_RECORD_ACCESS`; mature mail libraries in Python (`email`, `aioimaplib`, `dkimpy`, `pyspf`).
- **PostgreSQL** as the database — the canonical Django pairing, and required for some queue/scheduler options under consideration.
- **Server-rendered UI with HTMX**, no SPA. The admin and member-facing surfaces are forms-heavy with little interactivity; HTMX covers the "feels live" parts (approval queue, list-record edit) without a separate frontend toolchain.

### Task queue / scheduler

**`procrastinate`** — Postgres-native task queue built on `LISTEN`/`NOTIFY`. Chosen over Celery + Redis for three concrete reasons:

1. **Transactional enqueue** in the same commit as the originating Inbound/Outbound row write — eliminates the queue-vs-database race where a row exists but its task never ran (or vice versa). For a system whose correctness depends on message bookkeeping, this matters.
2. **Tasks are inspectable as ordinary Postgres rows** — debugging "why did this mail not get forwarded?" is a SQL query, not a Redis introspection problem.
3. **One fewer service** on volunteer-run hardware: no Redis.

Responsibilities:

- Outbound SMTP submission with retry (exponential backoff for transient SMTP errors).
- Release-mail dispatch when a sender-is-member Inbound row is created.
- Admin-review dispatch when a sender-is-not-member Inbound row is created.
- Forwarding fan-out after the release link is clicked (one task per recipient, parallelisable).
- Reply-routing for anonymization aliases (incoming mail to `alias-<token>@<domain>` → resolve → send to original sender).
- DSN correlation against the Outbound table.
- Periodic IMAP `EXPUNGE` of processed messages past the 7-day grace period.
- Periodic pruning of Inbound/Outbound metadata past retention.
- Release-token expiry.

Periodic tasks use `procrastinate`'s built-in periodic-task decorator — no separate scheduler process is needed (no Celery-Beat equivalent in the stack).

### Long-lived IMAP IDLE consumer

Django does not ship with a long-lived connection model, and `procrastinate`'s worker is designed for discrete tasks, not streaming. The IMAP IDLE consumer therefore runs as its **own dedicated process**: an **asyncio daemon** using `aioimaplib`, supervised by systemd. Its only job is to persist the Inbound row and enqueue downstream work to `procrastinate`; it performs no business logic itself, which keeps the streaming-IO process boring and pushes all retryable / correctness-critical work into the queue worker where retries are designed for it.

The daemon must implement: explicit reconnect-with-backoff on connection drop, a fallback periodic `SEARCH`/`NOOP` poll every few minutes for missed IDLE notifications, and idempotency keyed on IMAP UID so a reconnect cannot create duplicate Inbound rows.

## Notes for future work

- When implementing, model `LIST_RECORD_ACCESS` audiences as a `List_ID` foreign key on the access row, with `NULL` (mapping the spec's `0`) meaning "public" — the spec leans on this primitive throughout the visibility logic.
- The spec is the source of truth for product behavior. If a question can be answered by re-reading `filink.md`, do that before guessing.
