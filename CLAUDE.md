# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

The repository is initialized on branch `main` and pushed to GitHub (`origin = https://github.com/nzcaos/fi-link.git`). It currently contains the specification (`filink.md`, in German), the architecture decisions captured in this file, the deployment-procedure documentation in `README.md`, and a `.gitignore` for the planned Python/Django stack. There is no application code, build system, dependency manifest, or test suite yet — but the architecture is decided end-to-end (mail layer, application stack, task queue, IMAP IDLE consumer, domain model, authentication, deployment) and implementation is the next concrete step.

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
- **Invitation**: name and email prefilled from the inviter's input; on registration the stub USER (created earlier by the inviter) is activated and `RECORD_MANAGER` is updated with `basis=invited`. The invitation runs as a `LIST_INVITE_TOKEN` — see *Member invitation and LIST_INVITE_TOKEN* below for the full model that also covers existing-USER invitations.

### Member invitation and LIST_INVITE_TOKEN

Agreed 2026-05-26. Joining a list always runs through a single token type, **`LIST_INVITE_TOKEN`**, which subsumes both the not-yet-USER case (passkey enrollment) and the existing-USER case (one-click join with no passkey ceremony and no re-capture of personal data).

| Column | Meaning |
|---|---|
| `token` | random URL-safe string |
| `list_id` | the list to join |
| `invited_by_user_id` | LIST_ADMIN or super-admin who created the invitation |
| `target_email` | the address the invitation is sent to; also the prefill for the email field in the not-yet-USER branch |
| `target_person_id` | nullable; set when the invitation targets a specific existing PERSON (picked via USER-autocomplete in the admin UI) |
| `mode` | `self` or `via_associate` — wizard branch; defaults to the LISTTEMPLATE default but may be overridden per invitation |
| `created_at`, `expires_at` (default +30 days), `consumed_at` | lifecycle |

**Click-time branching by `target_person_id`:**

- `target_person_id IS NULL` — the today-already-documented flow: the recipient runs through passkey enrollment, the stub USER is activated (PERSON + USER created if not already), and the onboarding wizard for the list runs (`self` or `via_associate` per `mode`). Used for inviting people who are not yet on the system. `RECORD_MANAGER` is written with `basis=invited`.
- `target_person_id IS NOT NULL` — the recipient is already a USER. Click resolves to: ensure the visitor is authenticated as a USER linked to this PERSON. If not logged in, the standard passkey-login flow runs first (discoverable credentials, no enrollment); a wrong-USER session is rejected with "this invitation is for X, please sign in as them". After auth, a `LIST_RECORD` (role per `mode`/LISTTEMPLATE, subject = the target PERSON) is created with values prefilled from the PERSON (name) and from the USER's existing records in other lists where the same `LIST_ATTRIBUT` keys exist. The visitor lands **directly in the record-edit form** and the join is finalised by saving the form — no empty phantom record is left behind if the user abandons. A `RECORD_MANAGER` row with `basis=invited` is written on save. **No new passkey, no re-capture of personal data.**

Concrete example: at the start of a new school year the previous Elternvertreter (or the Vorsitz Elternbeirat, or the Elternvertreter-list admin) creates a `LIST_INVITE_TOKEN` for the newly elected Elternvertreter, picking them by name from a USER-autocomplete. The invitee gets a mail with one click-link → passkey login (single tap) → record-edit form pre-filled with their name → save → joined. No double-registration, no second passkey, no data re-entry.

**Editability of `target_email`** in the not-yet-USER branch: the recipient may correct the email at click time (e.g. they want their own address rather than the shared family one the inviter typed) — same field, same form, the address they submit becomes `PERSON.email`. In the existing-USER branch the email field is not shown — PERSON-email changes belong to the regular account-edit surface.

This token does **not** replace `ADMIN_INVITE_TOKEN` (see *Admin handover*). LIST_INVITE_TOKEN grants membership; ADMIN_INVITE_TOKEN grants admin rights with handover/add semantics. A USER may receive both at different times, or be promoted later via an ADMIN_INVITE_TOKEN after first joining as a member.

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

### School-class lifecycle

Agreed 2026-05-25. School class lists need lifecycle support that other list templates don't: classes advance one grade each year, students switch classes, and admins (Elternvertreter) rotate annually. These are first-class operations, not workarounds.

#### Cohort metadata

Class lists carry cohort metadata used by the rollover function:

- **`cohort_grade`** (5..12) — current grade level
- **`cohort_track`** (`a|b|c|d|…|null`) — the parallel-class letter; null for Kursstufe (K1/K2) where the class unit dissolves
- **`curriculum_track`** (`G8` | `G9`) — determines the rollover path

These fields belong to the school-class LISTTEMPLATE specialisation, not to every LIST. Other templates (Förderverein, VHS-Kurs) leave them null and don't participate in rollover.

#### Rollover function

A super-admin-triggered batch operation, typically run in the last week of the summer holidays. The UI lists all class lists with the proposed action; the super-admin reviews and adjusts, then executes. No automatic cron — the school decides when.

Standard rules:

- `Na → (N+1)a` for `N ∈ {5, …, 9}` (both tracks)
- **G8:** `10a..10d → K1` (N:1 merge), `K1 → K2`, `K2 → archived`
- **G9:** `10a → 11a`, …, `11a..11d → K1`, `K1 → K2`, `K2 → archived`

Implementation notes:

- LISTs are not deleted: rolled-up lists keep their identity, only title and cohort fields update. USERs and their LIST_RECORDs stay linked across the rollover.
- N:1 merges create a new K1 LIST and re-parent all member/associate LIST_RECORDs from the source lists onto it. Source lists become archived (read-only, `archived_at` set).
- The email alias rolls with the list (`5a@…` → `6a@…`) atomically at the moment of rollover. **No grace period:** from day 1 of the new school year only the new alias works; mail to the old alias bounces with a standard NDR. The summer holidays are the recipients' adjustment window.

**G8/G9 default for new lists:**

- A newly created grade-5 list defaults to `G9` (current school-system state in the target region).
- At initial system setup, the school's pre-existing classes (grades 6–12 at install time) default to `G8` and must be reviewed by the super-admin. The G8 pool drains naturally over time as those cohorts archive after K2.

#### Class transfer (single PERSON)

Distinct from cohort rollover: a single student moves between classes (repeats a grade, switches track for Bilingual / Naturwissenschaften, etc.). **Two-step flow with destination-admin consent:**

- Initiated by the source-list admin, by a `RECORD_MANAGER` of the moving PERSON, or by the PERSON themselves if they are a USER → creates a **`PENDING_TRANSFER`** row: `from_list_id`, `to_list_id`, `person_id`, `requested_by_user_id`, `requested_at`.
- The destination-list admin sees it in their approval UI and accepts or rejects.
- On acceptance: the LIST_RECORD is re-parented to the destination list; all associate LIST_RECORDs (parents/guardians via PERSON_RELATIONSHIP) are migrated alongside; an audit row is written. Field values carry over 1:1 (same LISTTEMPLATE assumed — cross-template moves are not supported in v1).
- Old LIST_RECORDs are archived (`archived_at` set), not deleted — keeps a trail for correction and recovery.

**Self-removal** (USER opts out of a list) is **not** transfer-gated: a USER can remove themselves — or, via `RECORD_MANAGER`, PERSONs they manage — from a list without anyone's consent. The record is archived. Exception: if the removal would leave the list with zero admins, it is blocked with a "handover required first" message (see *Admin handover*).

Super-admin can perform transfers and removals without consent.

### Admin handover

Agreed 2026-05-25. Elternvertreter are re-elected yearly; often the incumbent stays, sometimes not. The handover function:

- Implemented as **`ADMIN_INVITE_TOKEN`**: `token`, `list_id`, `from_user_id` (nullable; null when initiated by super-admin), `to_email`, `mode ∈ {handover, add}`, `created_at`, `expires_at` (default +30 days), `consumed_at`.
- The current admin (or super-admin) enters the successor's email and picks `handover` or `add`. System sends an invitation mail with the token link.
- Click + successful login or passkey enrollment → token consumed, new LIST_ADMIN row inserted. In `handover` mode the initiating admin's LIST_ADMIN row is removed in the same transaction.
- **Authentication is mandatory** — a click alone never confers admin rights. If the recipient is not yet a USER, the registration / passkey-enrollment flow is prepended (same activation flow as the family-triade invite). This prevents "compromised mailbox = instant admin takeover".
- An admin cannot remove themselves (via self-removal or `handover`) if doing so would leave the list with zero admins — they must `add` a successor first, or use `handover` with a defined target.

### Aggregate email aliases

Agreed 2026-05-25. Beyond per-list addresses, the system supports addresses that fan out across multiple lists by relationship role — e.g. `eltern@<domain>` reaches every PERSON with role `Mutter`, `Vater`, `Erziehungsberechtigte` within a configured subtree. Modeled as **`AGGREGATE_ALIAS`** (not a LIST — it has no membership, only a query):

| Column | Meaning |
|---|---|
| `email_alias` | e.g. `eltern@<domain>` |
| `title` | display name |
| `scope_list_id` | nullable; if set, restricts to PERSONs associated with members of this list or its sub-lists (entire subtree) |
| `included_roles` | array of role strings from the PERSON_RELATIONSHIP taxonomy |
| `created_by_user_id`, `created_at` | audit |

Configuration is super-admin only.

**Recipient resolution is at send time, not cached**: SQL over `PERSON_RELATIONSHIP` filtered by role, scoped via subtree traversal of `scope_list_id`, dedup'd per PERSON, restricted to `PERSON.email IS NOT NULL`.

**Send permission is derived from the existing `LIST_SEND_PERMISSION` graph** — there is no separate permission table on `AGGREGATE_ALIAS`. Algorithm:

1. Resolve the aggregate alias to its set of target LISTs (all lists in the `scope_list_id` subtree whose associates carry the filtered roles).
2. Sender is permitted if and only if they hold send permission (direct or transitive via hierarchy) on **every** list in that set.
3. If permitted: forward, with `requires_release_click` resolved as the **strictest** of the per-list values (any one list requiring a release click triggers it).
4. Otherwise: route to **super-admin approval** (not list-admin approval — aggregate aliases have no list-admin; the super-admin owns them).

Concrete example: the Vorsitz Elternbeirat is a member of the Elternbeirat list, which is `parent_list_id` of every class list, so they hold implicit send permission on every class → eligible to send to `eltern@…` directly. An ordinary class-level Elternvertreter has no permission on other classes → their attempt to send to `eltern@…` lands in super-admin approval.

All other mail-pipeline behavior (anti-spoofing, anti-loop, bounce handling, anonymization, fan-out via procrastinate) is identical to regular lists.

### Encryption at rest

`LIST_RECORD_VALUE.value` is encrypted at the application layer with Fernet (AES-128-CBC + HMAC), using a **single installation-wide key** from an environment variable. **All values are encrypted unconditionally** — no per-field opt-in/out, to eliminate the risk of forgetting. Library: `django-cryptography` or equivalent.

Trade-off: SQL `WHERE` / `ORDER BY` / `LIKE` on encrypted columns is no longer available. Acceptable for Fichtelink — the UI shows lists row-by-row, and full-text search across PII was never a feature. Key rotation is supported via the library's multi-key mechanism (old + new active simultaneously, re-encrypt over time).

`PERSON.email` remains plaintext — it is the addressable identifier used for activation links, SMTP routing of forwarded mail, In-Reply-To correlation, bounce addressing, and as From/To in mail headers we send out. Disk-level encryption (e.g. LUKS on the VPS) is orthogonal and a deployment concern.

## Architecture decisions (authentication)

Agreed with the project owner on 2026-05-24. **Library choice revised 2026-05-25** after reviewing the `Fi-Planer` sibling project (also volunteer-run, also school-context, also Passkey-only) which has the same flow in production. Where this section overlaps with Fi-Planer's `CLAUDE.md`, that's deliberate — same operational lessons apply.

### Passkeys only, no passwords

Authentication is **exclusively** via WebAuthn passkeys. No password storage, no password-reset flow, no password-based login backend. Rationale: passwords can be forgotten, chosen poorly, reused across services, and leaked in third-party breaches — none of these failure modes apply to passkeys.

Trade-off: users without passkey-capable devices (older Windows, no smartphone) cannot use the system at all. This is accepted deliberately — the volunteer-run target audience is overwhelmingly on devices that support passkeys (iOS 16+, Android 9+, modern macOS / Windows / Linux with current browsers), and the security gain is judged to outweigh the exclusion. Setup documentation must call this requirement out clearly.

### Library choice and topology

**Hand-rolled, not `django-allauth`.** An earlier plan named `django-allauth` (with its WebAuthn MFA module) as the auth library. That has been dropped after weighing it against our actual constraints:

- allauth is centered on `User.email`. We have `Person.email`, non-unique (multiple USERs may share a family mailbox per *PERSON vs. USER*), and login resolves to a USER *set* keyed by the chosen passkey — not by email. Fitting that into allauth would mean overriding most of its email-centric flow.
- Allauth brings its own templates, views, and signup/login state machine — which would all need overriding to fit the triade-invitation flow, the super-admin bootstrap, and the shared-family-email case.
- The hand-rolled alternative is small (~400 LOC Python + ~100 LOC frontend glue, scaled from Fi-Planer's `auth.js`) and uses Django's built-in sessions / CSRF / cookies for everything around the WebAuthn ceremony itself.

The libraries:

- **Server: `py_webauthn` (Duo Labs)**, pinned to **v2.x**. Same API shape as Fi-Planer's `@simplewebauthn/server` v9 (`generate_registration_options`, `verify_registration_response`, `generate_authentication_options`, `verify_authentication_response`). Pin to the major to avoid v3-style breaking renames.
- **Client: `@simplewebauthn/browser` v9.x**, shipped as a **vendored UMD bundle** in `fichtelink/static/vendor/simplewebauthn-browser-9.x.x.umd.min.js`. **No CDN delivery.** Fi-Planer observed mobile-device flakiness when WebAuthn JS came from a public CDN; self-hosting eliminates that failure mode and removes a third-party dependency from the security-critical login path. Whitenoise serves the bundle from `STATIC_ROOT`.

In the page: a plain `<script src="/static/vendor/simplewebauthn-browser-9.x.x.umd.min.js">` provides the `SimpleWebAuthnBrowser.startRegistration(opts)` / `startAuthentication(opts)` globals. No build step, no module loader, no CDN.

### Challenge storage

Each `register-begin` / `login-begin` returns a challenge that must be presented again at `finish` time. Storage: **a Postgres table `webauthn_challenge`** (`challenge bytea PRIMARY KEY`, `purpose ENUM('register','login')`, `expected_user_id` nullable for discoverable-credential login, `created_at`, `expires_at` default +5 min). A `procrastinate` periodic task sweeps expired rows.

Rejected: the in-memory `Map` pattern Fi-Planer uses. For Fichtelink the pending-registration rate is low (one per invitation acceptance), but losing a pending registration to a gunicorn worker restart is an avoidable irritation in a multi-worker setup. Postgres also keeps the challenge visible to all workers, which an in-memory store does not.

### Enumeration resistance

`login-begin` returns a **constant-time response** (≥ 250 ms floor, fake `allowCredentials` for unknown emails) regardless of whether the email is known. Same pattern as Fi-Planer (`backend/src/routes/auth.js:148`).

`register-begin` is intentionally **not** enumeration-resistant — telling a returning user that their email already has an account is part of the invitation/recovery UX (they need to be steered to the device-loss flow, not silently re-registered). Same trade-off Fi-Planer makes (`auth.js:166`).

For the shared-family-mailbox case: `login-begin` resolves an email to a *set* of USERs and emits `allowCredentials` containing every passkey of every matching USER. The browser shows all available passkeys; the user picks their own; the chosen credential uniquely identifies the USER.

### Registration and login flow

- **Registration:** user enters email → confirmation link sent to that address → on click, the browser prompts for passkey enrollment (Touch ID, Face ID, Windows Hello, or a hardware security key) → account is active.
- **Invitation (family-triade activation):** the invitation link *is* the confirmation link — clicking it activates the stub USER previously created by the inviter and triggers passkey enrollment in a single step.
- **Login:** preferred path is the *usernameless* / discoverable-credentials flow — the user clicks "Sign in", the browser presents available passkeys (labeled by WebAuthn `user.displayName`, e.g. "Anna Müller — Fichtelink"), the user picks one, the corresponding USER is signed in. No email entry needed. The login email-input field carries `autocomplete="email webauthn"` for the platform's conditional-UI / autofill path.
- **Multiple passkeys per account** are encouraged: the UI lets users enroll passkeys on additional devices (e.g. "iPhone", "Arbeit-Laptop") and delete individual ones. Multi-device enrollment is the primary defense against device loss and reduces the recovery burden.

**Email is mandatory at every entry point, not optional** (decided 2026-05-26). Fichtelink's core purpose is mail-mediated communication; without an address a USER cannot receive list mail, release-click confirmations, or activation links. Concretely:

- **QR-driven self-onboarding**: the email field is required. The form cannot be submitted without an address.
- **Invitation acceptance for a not-yet-USER** (`LIST_INVITE_TOKEN.target_person_id IS NULL`): the email is **prefilled** from `target_email` but **editable** — the recipient may correct it (e.g. swap the shared family address for their personal one) — and remains required.
- **Invitation acceptance for an existing USER** (`target_person_id IS NOT NULL`): the email field is not shown at all; the existing `PERSON.email` is unchanged by the join.

Wherever the email field is shown to a user during onboarding, a short inline privacy hint appears directly beneath it: *"Ihre E-Mail wird für die Kommunikation mit Ihnen verwendet und nur sichtbar, wenn Sie sie pro Liste explizit freigeben."* This makes the trade-off explicit: providing the address is required for the system to function, but disclosure to other list members is opt-in per list via the standard per-field visibility matrix.

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

### HTTPS, RP config, and the Apache proxy

WebAuthn works only over HTTPS (or `localhost` for development). TLS is therefore a hard deployment requirement, not optional. Recommended path for the volunteer-run target audience: Let's Encrypt with automatic renewal via the hosting provider's standard tooling. Setup documentation must call this out — a deployment without HTTPS cannot log anyone in.

The RP identity is configured via three environment variables (read at startup in `settings.py`):

- `RP_ID` — bare hostname, **no scheme**, e.g. `fichtelink.caos.cloud`.
- `RP_ORIGIN` — full URL the browser uses, e.g. `https://fichtelink.caos.cloud`. Must exactly match what the browser sees.
- `RP_NAME` — display name shown by the authenticator (e.g. "Fichtelink").

Operational gotchas (all reproduced from Fi-Planer's experience — see its `CLAUDE.md` §Topology):

- Apache vhost **must** set `ProxyPreserveHost On`. Without it the proxied request reaches Django with `Host: <vm-ip>` and the WebAuthn origin check silently fails — the browser ceremony completes successfully and the server then returns 400 with a misleading "origin mismatch" error.
- Apache `ServerName` must exactly match `RP_ID`. A mismatch produces the same silent failure mode.
- The proxy must be root-pathed (`ProxyPass /`) — anything fancier breaks static asset paths, including the vendored SimpleWebAuthn bundle.
- Django reads `X-Forwarded-Proto` via `SECURE_PROXY_SSL_HEADER` so it generates `https://` URLs for activation links and the WebAuthn challenge sees the correct origin.

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

- **Django** as the web framework. Rationale: Python is widely known in the volunteer maintainer pool, which matters for long-term open-source maintainability; Django Admin is a free MVP for super-admin work on `LISTTEMPLATE`; `py_webauthn` covers passkey-only authentication in a hand-rolled flow (see *Architecture decisions (authentication)*); `django-guardian` maps onto the per-object visibility model in `LIST_RECORD_ACCESS`; mature mail libraries in Python (`email`, `aioimaplib`, `dkimpy`, `pyspf`).
- **PostgreSQL** as the database — the canonical Django pairing, and required for some queue/scheduler options under consideration.
- **Server-rendered UI** — see *Frontend* below.

### Frontend

**HTMX + Django templates** as the primary rendering and interactivity layer. The server returns HTML (full pages or fragments), HTMX swaps fragments into the DOM based on `hx-*` attributes. No SPA, no virtual DOM, no JSON API layer for the UI. The admin and member-facing surfaces are forms-heavy and request/response-shaped — HTMX covers the "feels live" parts (approval queue, list-record edit, onboarding wizard) without a separate frontend toolchain.

**Alpine.js** as the standard partner to HTMX for client-side state sprinkles — toggles, dropdowns, the per-field visibility matrix, conditional form sections. ~10 KB, no build step, attribute-driven (`x-data`, `x-show`, `x-on`).

**Hand-written JavaScript** is kept minimal — WebAuthn ceremonies (via `@simplewebauthn/browser` v9, vendored as a UMD bundle in `static/vendor/` and loaded as a plain `<script>` tag; see *Architecture decisions (authentication) / Library choice*), QR-code generation for invitation links (`qrcode.js`, same vendored-UMD pattern), small Alpine helpers. Expected total volume well below the threshold where a build pipeline would pay back. **No CDN delivery for any of these** — vendored from the project's `static/vendor/` and served by Whitenoise.

**TypeScript is deferred, not adopted.** Trigger to revisit: hand-written client JS exceeding ~500 lines, or emerging as a cohesive library worth typing (e.g. a typed WebAuthn wrapper plus reusable components). Migration would be a single-binary `esbuild` step in a Docker multi-stage build — non-breaking for everything else.

**Vue (and any other SPA framework) is explicitly out of scope** — it would require a JSON API layer (DRF or django-ninja), a Node build pipeline, and a parallel rendering tree, contradicting the "server-rendered, no SPA" decision. Reconsider only if a specific future surface emerges that genuinely needs reactive component-tree thinking (none does today).

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

## Architecture decisions (deployment)

Agreed 2026-05-25. Operational procedure (commands, env vars, day-2 ops) lives in `README.md`; this section captures the *decisions* behind that procedure.

### Container topology

Four services in one `docker-compose.yml`, **all built from a single shared image** (same Python dependencies, differing only in `command:`):

- `db` — PostgreSQL 16, persistent named volume.
- `web` — Django + gunicorn.
- `worker` — `procrastinate` worker; also handles periodic tasks via the built-in decorator (no separate scheduler service).
- `imap_idle` — long-lived asyncio `aioimaplib` daemon.

One shared image rather than per-service Dockerfiles: the IMAP daemon's footprint (Python interpreter + Django settings + DB driver) is identical to the web container, and a single build is simpler to reason about than four.

### Reverse-proxy boundary

TLS is terminated by an **Apache reverse-proxy on a separate host in the same private network** as the VM (existing infrastructure on the target deployment — `fichtelink.caos.cloud`, Let's Encrypt cert managed there). The `web` container binds plain HTTP on a port reachable **only from the reverse-proxy host** (VM firewall enforces). Rationale: TLS, cert renewal, and SNI/multi-vhost concerns are already a solved problem on the proxy box; duplicating them inside the Docker stack would add complexity for no gain on this deployment scale.

Django configuration honours `X-Forwarded-Proto` (`SECURE_PROXY_SSL_HEADER`, `USE_X_FORWARDED_HOST`) so it generates `https://` URLs for activation/release links and the WebAuthn challenge. **WebAuthn RP-ID and origin are the browser-visible name** (`fichtelink.caos.cloud` / `https://fichtelink.caos.cloud`) — the proxy is transparent to the browser, no special WebAuthn config needed for the proxy.

### Static and media files

**Static files served by WhiteNoise** inside the `web` container, not by the reverse-proxy. The asset volume is small enough that serving from the Python process has no measurable overhead, and it removes one cross-container coupling. FORM_PART_ASSET uploads land on a host-mounted `media` volume.

### Secrets

A single `.env` file on the VM, **never in git**. The repository ships `.env.example` with placeholders. Keys are documented both there and in README.md. The `FERNET_KEY` is particularly critical — losing it makes all encrypted `LIST_RECORD_VALUE` rows unrecoverable; operator must back it up off-host.

### Deploy flow

Manual `git pull` + `docker compose` on the VM, **no CI/CD in v1**. Rationale: the target is a single VM operated by one person; GitHub Actions + a container registry adds infrastructure complexity (image-tag management, pull-credentials, registry auth) without solving a problem at this scale. Migration to pre-built images via GHA is a non-breaking change later — only the `build:` / `image:` directive in the compose file changes.

Migrations and `collectstatic` are explicit `docker compose run --rm` steps in the deploy procedure, **not auto-on-start**. Auto-migrate on startup risks restart loops if a migration is broken and obscures the failure; explicit steps make the deploy procedure inspectable and recoverable.

### Super-admin bootstrap

Management command `manage.py bootstrap_super_admin`, invoked once at install time. It prompts for an email, creates a USER + PERSON marked as super-admin, and sends a passkey-enrollment link. There is no password — activation is by link only, identical to the regular invitation flow. Same pattern is used for `manage.py reset_passkeys --email <…>` in the manual-recovery path.

## Code conventions

Agreed 2026-05-25.

**Model class names are English; `verbose_name` is German.** The spec uses German vocabulary (`Liste`, `Vorlage`, `Eintrag`, `Benutzergruppe`, …); the implementation uses Django-idiomatic English class names (`List`, `ListTemplate`, `ListRecord`, `ListAccess`, …) and exposes the German vocabulary to users via each model's `verbose_name`, `verbose_name_plural`, and field `verbose_name`/`help_text`. Reason: mixed English/German imports get noisy fast, and Django's own classes (`AbstractBaseUser`, `Model`, `ForeignKey`) are English-rooted regardless. The German vocabulary is preserved everywhere a user sees it (Django Admin, future templates, error messages).

Field names follow the same rule (English identifier, German `verbose_name`). One exception: `Person` and `User` keep both their English name and English vocabulary, because the spec itself uses these terms in English.

## Notes for future work

- When implementing, model `LIST_RECORD_ACCESS` audiences as a `List_ID` foreign key on the access row, with `NULL` (mapping the spec's `0`) meaning "public" — the spec leans on this primitive throughout the visibility logic.
- The spec is the source of truth for product behavior. If a question can be answered by re-reading `filink.md`, do that before guessing.
